#!/usr/bin/env python3
"""Check Skill structure, suite contracts, and high-signal behavior risks."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys
from typing import Any

from forge_core import ForgeError, digest_json, inspect_tree, load_suite, tree_digest, write_json_atomic


CACHE_NAMES = {".DS_Store", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SENSITIVE_NAMES = {".env", "credentials", "credentials.json", "id_rsa", "id_ed25519"}
LOCAL_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return {}, ["SKILL.md must be UTF-8"]
    if not lines or lines[0].strip() != "---":
        return {}, ["SKILL.md must start with YAML frontmatter"]
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}, ["SKILL.md frontmatter is not closed"]
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            errors.append(f"invalid frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in values:
            errors.append(f"duplicate frontmatter field: {key}")
        values[key] = value
    unknown = sorted(set(values) - {"name", "description"})
    if unknown:
        errors.append(f"frontmatter contains unsupported fields: {', '.join(unknown)}")
    if not values.get("name"):
        errors.append("frontmatter.name is required")
    if not values.get("description"):
        errors.append("frontmatter.description is required")
    return values, errors


def _local_links(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    links: list[str] = []
    for match in LOCAL_LINK.finditer(content):
        target = match.group(1).split("#", 1)[0].strip()
        if target and "://" not in target and not target.startswith("#"):
            links.append(target)
    return links


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


class RiskVisitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.findings: list[dict[str, Any]] = []

    def add(self, node: ast.AST, rule: str, dimension: str, detail: str) -> None:
        self.findings.append(
            {
                "path": self.relative,
                "line": getattr(node, "lineno", None),
                "rule": rule,
                "dimension": dimension,
                "detail": detail,
            }
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".", 1)[0] in {"requests", "httpx", "socket", "urllib"}:
                self.add(node, "network-import", "real_external_dependency", alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module.split(".", 1)[0] in {"requests", "httpx", "socket", "urllib"}:
            self.add(node, "network-import", "real_external_dependency", module)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func) or ""
        if name in {"eval", "exec", "compile"}:
            self.add(node, "dynamic-code", "high_impact_evaluation", name)
        if name == "os.system" or name.startswith("subprocess."):
            self.add(node, "process-execution", "local_mutation", name)
        if name in {"os.remove", "os.unlink", "shutil.rmtree", "Path.unlink", "Path.rmdir"}:
            self.add(node, "destructive-filesystem", "irreversible_change", name)
        if name.startswith(("requests.", "httpx.", "urllib.", "socket.")):
            self.add(node, "network-call", "real_external_dependency", name)
        for keyword in node.keywords:
            if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                self.add(node, "shell-true", "high_impact_evaluation", name or "call")
        self.generic_visit(node)


def inspect_skill(root: Path, suite_path: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    errors: list[str] = []
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    try:
        entries = inspect_tree(root, reject_unsafe=False)
    except ForgeError as exc:
        return {
            "version": 1,
            "skill_root": str(root),
            "structural_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "risk_findings": [],
            "risk_unknowns": [],
            "tree_digest": None,
            "suite_digest": None,
        }
    paths = {entry.path: entry for entry in entries}
    for entry in entries:
        path = Path(entry.path)
        if entry.kind in {"symlink", "special"}:
            errors.append(f"unsafe {entry.kind}: {entry.path}")
        if any(part in CACHE_NAMES for part in path.parts) or path.suffix == ".pyc":
            errors.append(f"generated/cache file is not allowed: {entry.path}")
        lowered = path.name.lower()
        if lowered in SENSITIVE_NAMES or path.suffix.lower() in {".pem", ".key", ".p12"}:
            errors.append(f"sensitive-looking file is not allowed: {entry.path}")
    skill_md = root / "SKILL.md"
    if "SKILL.md" not in paths or not skill_md.is_file():
        errors.append("SKILL.md is required")
        metadata: dict[str, str] = {}
    else:
        metadata, frontmatter_errors = _frontmatter(skill_md)
        errors.extend(frontmatter_errors)
        if metadata.get("name") and metadata["name"] != root.name:
            errors.append("frontmatter.name must match the Skill directory name")
        content = skill_md.read_text(encoding="utf-8")
        if "TODO" in content or "[TODO" in content:
            errors.append("SKILL.md contains placeholder TODO text")
        if len(content.splitlines()) > 500:
            warnings.append("SKILL.md exceeds 500 lines")
        for target in _local_links(skill_md):
            try:
                resolved = (root / target).resolve(strict=True)
                resolved.relative_to(root)
            except (FileNotFoundError, ValueError):
                errors.append(f"SKILL.md has missing or escaping local reference: {target}")
    references = root / "references"
    if references.is_dir():
        for path in sorted(references.rglob("*.md")):
            relative = path.relative_to(root).as_posix()
            if "TODO" in path.read_text(encoding="utf-8"):
                errors.append(f"reference contains placeholder TODO text: {relative}")
            nested = _local_links(path)
            if nested:
                warnings.append(f"nested local references should be linked directly from SKILL.md: {relative}")
    scripts = root / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix == ".py":
                try:
                    parsed = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
                except (SyntaxError, UnicodeDecodeError) as exc:
                    unknowns.append(
                        {"path": relative, "reason": "python_parse_failed", "detail": str(exc)}
                    )
                    continue
                visitor = RiskVisitor(relative)
                visitor.visit(parsed)
                findings.extend(visitor.findings)
            elif path.name != "__init__.py":
                unknowns.append(
                    {"path": relative, "reason": "non_python_script_not_scanned"}
                )
    suite_digest: str | None = None
    if suite_path is not None:
        try:
            suite = load_suite(suite_path)
            suite_digest = digest_json(suite)
            for case in suite["cases"]:
                if case["fixture"] is not None:
                    fixture = (suite_path.parent / case["fixture"]).resolve(strict=True)
                    fixture.relative_to(suite_path.parent.resolve(strict=True))
                    if not fixture.is_dir():
                        raise ForgeError(f"fixture is not a directory: {case['fixture']}")
        except (ForgeError, FileNotFoundError, ValueError) as exc:
            errors.append(f"suite invalid: {exc}")
    try:
        skill_digest = tree_digest(root, reject_unsafe=True) if not errors else None
    except ForgeError as exc:
        errors.append(str(exc))
        skill_digest = None
    return {
        "version": 1,
        "skill_root": str(root),
        "skill_name": metadata.get("name"),
        "structural_valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "risk_findings": findings,
        "risk_unknowns": unknowns,
        "tree_digest": skill_digest,
        "suite_digest": suite_digest,
        "risk_statement": "findings and unknowns are planning evidence, not a safety certificate",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", type=Path)
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_skill(args.skill_root, args.suite)
    except (ForgeError, FileNotFoundError, NotADirectoryError) as exc:
        report = {
            "version": 1,
            "skill_root": str(args.skill_root),
            "structural_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "risk_findings": [],
            "risk_unknowns": [],
            "tree_digest": None,
            "suite_digest": None,
        }
    if args.output:
        write_json_atomic(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["structural_valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
