#!/usr/bin/env python3
"""Build and verify a host distribution of Skill Forge from current bytes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from typing import Any

from forge_core import (
    ForgeError,
    digest_file,
    digest_json,
    inspect_tree,
    tree_digest,
    write_json_atomic,
)


HOSTS = ("codex", "claude")

# Runtime payload only. Development fixtures, tests, and repository docs are
# authoring inputs and never ship.
SHARED_RULES = ("SKILL.md", "VERSION", "LICENSE", "references/", "scripts/")
HOST_RULES: dict[str, tuple[str, ...]] = {
    # agents/openai.yaml declares the Codex interface; Claude reads frontmatter.
    "codex": SHARED_RULES + ("agents/",),
    "claude": SHARED_RULES,
}
EXCLUDED_SCRIPTS = frozenset({"package.py"})
CACHE_PARTS = frozenset({"__pycache__", ".DS_Store", ".pytest_cache", ".mypy_cache"})


def _selected(relative: str, rules: tuple[str, ...]) -> bool:
    for rule in rules:
        if rule.endswith("/"):
            if relative.startswith(rule):
                return True
        elif relative == rule:
            return True
    return False


def _projection(source: Path, rules: tuple[str, ...]) -> tuple[list[str], list[dict[str, str]]]:
    """Select payload paths from current source bytes, recording what was skipped."""
    selected: list[str] = []
    ignored: list[dict[str, str]] = []
    for entry in inspect_tree(source, reject_unsafe=False):
        relative = entry.path
        parts = PurePosixPath(relative).parts
        if entry.kind != "file":
            if entry.kind in {"symlink", "special"}:
                ignored.append({"path": relative, "reason": f"unsafe_{entry.kind}"})
            continue
        if any(part in CACHE_PARTS for part in parts) or relative.endswith(".pyc"):
            ignored.append({"path": relative, "reason": "generated_cache_artifact"})
            continue
        if parts[0] == "scripts" and parts[-1] in EXCLUDED_SCRIPTS:
            ignored.append({"path": relative, "reason": "packaging_tool_not_shipped"})
            continue
        if not _selected(relative, rules):
            ignored.append({"path": relative, "reason": "outside_host_whitelist"})
            continue
        selected.append(relative)
    if "SKILL.md" not in selected:
        raise ForgeError("distribution payload is missing SKILL.md")
    return sorted(selected), ignored


def _read_only(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _seal_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            _read_only(Path(current) / name)
        for name in directories:
            _read_only(Path(current) / name)
    _read_only(root)


def build(source: Path, host: str, output: Path, manifest_path: Path) -> dict[str, Any]:
    source = source.expanduser().resolve(strict=True)
    if host not in HOST_RULES:
        raise ForgeError(f"unsupported host: {host}")
    output = output.expanduser().resolve(strict=False)
    manifest_path = manifest_path.expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise ForgeError(f"distribution destination already exists: {output}")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ForgeError(f"manifest destination already exists: {manifest_path}")
    if source == output or output.is_relative_to(source):
        raise ForgeError("distribution destination must be outside the source tree")
    rules = HOST_RULES[host]
    before = tree_digest(source, reject_unsafe=False)
    selected, ignored = _projection(source, rules)
    output.mkdir(parents=True)
    files: list[dict[str, Any]] = []
    total = 0
    for relative in selected:
        origin = source / relative
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        size = destination.stat().st_size
        total += size
        files.append(
            {
                "path": relative,
                "digest": digest_file(destination),
                "size": size,
                "executable": bool(origin.stat().st_mode & stat.S_IXUSR),
            }
        )
    if tree_digest(source, reject_unsafe=False) != before:
        raise ForgeError("source tree changed while it was being packaged")
    # Seal before digesting: the tree digest covers file modes, so a manifest
    # written pre-seal would never match the shipped tree.
    _seal_read_only(output)
    projection = {"rules": list(rules), "ignored_paths": ignored}
    manifest = {
        "schema_version": 1,
        "object_version": "skill-forge.distribution-manifest/v1",
        "host": host,
        "skill": "skill-forge",
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
        "source_projection": projection,
        "source_projection_digest": digest_json(projection),
        "dist_digest": tree_digest(output, reject_unsafe=True),
    }
    write_json_atomic(manifest_path, manifest)
    _read_only(manifest_path)
    return manifest


def verify(candidate: Path, manifest_path: Path) -> dict[str, Any]:
    """Recheck a distribution against its manifest using current bytes."""
    candidate = candidate.expanduser().resolve(strict=True)
    manifest_path = manifest_path.expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ForgeError(f"invalid distribution manifest: {manifest_path}")
    entries = {
        entry.path: entry
        for entry in inspect_tree(candidate, reject_unsafe=True)
        if entry.kind == "file"
    }
    expected = {item["path"]: item for item in manifest["files"]}
    missing = sorted(set(expected) - set(entries))
    unexpected = sorted(set(entries) - set(expected))
    if missing:
        raise ForgeError(f"distribution is missing files: {', '.join(missing)}")
    if unexpected:
        raise ForgeError(f"distribution has unexpected files: {', '.join(unexpected)}")
    for relative, item in sorted(expected.items()):
        if entries[relative].digest != item["digest"]:
            raise ForgeError(f"distribution file digest changed: {relative}")
        if entries[relative].size != item["size"]:
            raise ForgeError(f"distribution file size changed: {relative}")
    observed = tree_digest(candidate, reject_unsafe=True)
    if observed != manifest["dist_digest"]:
        raise ForgeError("distribution tree digest does not match its manifest")
    writable = sorted(
        entry.path
        for entry in inspect_tree(candidate, reject_unsafe=True)
        if entry.mode & 0o222
    )
    return {
        "object_version": "skill-forge.distribution-receipt/v1",
        "dist_root": str(candidate),
        "manifest_path": str(manifest_path),
        "host": manifest["host"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "dist_digest": observed,
        "manifest_digest": digest_file(manifest_path),
        "source_projection_digest": manifest["source_projection_digest"],
        "tree_read_only": not writable,
        "writable_paths": writable,
        "claim_cap": "byte_binding_only",
        "limitations": [
            "not_a_signature",
            "not_installed",
            "host_activation_unverified",
            "read_only_is_removed_posix_write_bits",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build one host distribution")
    build_parser.add_argument("--source", type=Path, required=True)
    build_parser.add_argument("--host", choices=HOSTS, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify", help="verify one built distribution")
    verify_parser.add_argument("--candidate", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest = build(args.source, args.host, args.output, args.manifest)
            print(
                json.dumps(
                    {
                        "host": manifest["host"],
                        "file_count": manifest["file_count"],
                        "total_bytes": manifest["total_bytes"],
                        "dist_digest": manifest["dist_digest"],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            receipt = verify(args.candidate, args.manifest)
            if args.output:
                write_json_atomic(args.output, receipt)
            print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    except (ForgeError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        print(f"skill-forge package failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
