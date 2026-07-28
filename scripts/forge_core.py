#!/usr/bin/env python3
"""Shared strict-data and filesystem helpers for Skill Forge.

This module is private implementation support. The public commands are
``check.py``, ``run.py``, and ``score.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Iterable


MAX_SUITE_BYTES = 1_000_000
MAX_TREE_BYTES = 25_000_000
MAX_FILE_BYTES = 5_000_000
ALLOWED_MODES = frozenset({"create", "optimize", "no_skill"})
ALLOWED_SOURCES = frozenset(
    {"observed", "user_confirmed", "synthetic", "assumed"}
)
ALLOWED_PLANES = frozenset({"routing", "execution"})
ALLOWED_CATEGORIES = frozenset(
    {"core", "boundary", "near_negative", "failure"}
)
EXPECTATION_FIELDS = {
    "selected_skill": frozenset({"kind", "value"}),
    "exit_code": frozenset({"kind", "value"}),
    "stdout_equals": frozenset({"kind", "value"}),
    "stdout_contains": frozenset({"kind", "value"}),
    "stdout_not_contains": frozenset({"kind", "value"}),
    "file_exists": frozenset({"kind", "path"}),
    "file_not_exists": frozenset({"kind", "path"}),
    "file_sha256": frozenset({"kind", "path", "value"}),
    "json_equals": frozenset({"kind", "path", "value"}),
    "validator": frozenset({"kind", "path", "args", "timeout_seconds"}),
}
STRONG_EXECUTION_EXPECTATIONS = frozenset(
    {"stdout_equals", "file_sha256", "json_equals", "validator"}
)


class ForgeError(ValueError):
    """Raised when a Skill Forge input violates a closed contract."""


def _reject_constant(value: str) -> None:
    raise ForgeError(f"non-finite JSON number is not allowed: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ForgeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads_json_strict(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ForgeError:
        raise
    except (TypeError, json.JSONDecodeError) as exc:
        raise ForgeError(f"invalid JSON: {exc}") from exc


def read_json_strict(path: Path, *, max_bytes: int = MAX_SUITE_BYTES) -> Any:
    path = path.expanduser().resolve(strict=True)
    size = path.stat().st_size
    if size > max_bytes:
        raise ForgeError(f"JSON input exceeds {max_bytes} bytes: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ForgeError(f"JSON input is not UTF-8: {path}") from exc
    return loads_json_strict(text)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ForgeError(f"value is not canonical JSON data: {exc}") from exc


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(canonical_json_bytes(value))


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def require_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ForgeError(f"{field} must be a non-empty relative path")
    if "\\" in value or "\x00" in value:
        raise ForgeError(f"{field} must use a safe POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ForgeError(f"{field} must not be absolute or traverse parents")
    return path.as_posix()


def resolve_under(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    root = root.expanduser().resolve(strict=True)
    safe = require_relative_path(relative, "path")
    candidate = (root / safe).resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ForgeError(f"path escapes its root: {relative}") from exc
    return candidate


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    size: int
    digest: str | None
    mode: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "digest": self.digest,
            "mode": self.mode,
        }


def inspect_tree(root: Path, *, reject_unsafe: bool = False) -> list[TreeEntry]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ForgeError(f"tree root is not a directory: {root}")
    entries: list[TreeEntry] = []
    total = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(tuple(directories)):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if reject_unsafe:
                    raise ForgeError(f"symlink is not allowed: {relative}")
                entries.append(TreeEntry(relative, "symlink", 0, None, info.st_mode & 0o777))
                directories.remove(name)
            elif not stat.S_ISDIR(info.st_mode):
                if reject_unsafe:
                    raise ForgeError(f"special directory entry is not allowed: {relative}")
                entries.append(TreeEntry(relative, "special", 0, None, info.st_mode & 0o777))
                directories.remove(name)
            else:
                entries.append(TreeEntry(relative, "directory", 0, None, info.st_mode & 0o777))
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if reject_unsafe:
                    raise ForgeError(f"symlink is not allowed: {relative}")
                entries.append(TreeEntry(relative, "symlink", 0, None, info.st_mode & 0o777))
                continue
            if not stat.S_ISREG(info.st_mode):
                if reject_unsafe:
                    raise ForgeError(f"special file is not allowed: {relative}")
                entries.append(TreeEntry(relative, "special", 0, None, info.st_mode & 0o777))
                continue
            if info.st_size > MAX_FILE_BYTES:
                raise ForgeError(f"file exceeds {MAX_FILE_BYTES} bytes: {relative}")
            total += info.st_size
            if total > MAX_TREE_BYTES:
                raise ForgeError(f"tree exceeds {MAX_TREE_BYTES} bytes: {root}")
            entries.append(
                TreeEntry(
                    relative,
                    "file",
                    info.st_size,
                    digest_file(path),
                    info.st_mode & 0o777,
                )
            )
    return sorted(entries, key=lambda item: (item.path, item.kind))


def tree_digest(root: Path, *, reject_unsafe: bool = True) -> str:
    entries = inspect_tree(root, reject_unsafe=reject_unsafe)
    return digest_json([entry.to_dict() for entry in entries])


def snapshot_tree(source: Path, destination: Path) -> str:
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise ForgeError(f"snapshot destination already exists: {destination}")
    before = tree_digest(source, reject_unsafe=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=False)
    after_source = tree_digest(source, reject_unsafe=True)
    copied = tree_digest(destination, reject_unsafe=True)
    if before != after_source:
        raise ForgeError("source tree changed while it was being snapshotted")
    if copied != before:
        raise ForgeError("snapshot tree does not match its source")
    return copied


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ForgeError(f"{field} must be an object")
    return value


def _reject_unknown(mapping: dict[str, Any], allowed: Iterable[str], field: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ForgeError(f"{field} contains unknown fields: {', '.join(unknown)}")


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForgeError(f"{field} must be non-empty text")
    return value


def validate_suite(value: Any) -> dict[str, Any]:
    suite = _require_mapping(value, "suite")
    _reject_unknown(suite, {"version", "skill", "mode", "reps", "cases"}, "suite")
    if suite.get("version") != 1:
        raise ForgeError("suite.version must equal 1")
    skill = _require_text(suite.get("skill"), "suite.skill")
    if len(skill) > 63 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in skill):
        raise ForgeError("suite.skill must be lowercase hyphen-case")
    mode = suite.get("mode")
    if mode not in ALLOWED_MODES:
        raise ForgeError(f"suite.mode must be one of {sorted(ALLOWED_MODES)}")
    reps = suite.get("reps", 1)
    if isinstance(reps, bool) or not isinstance(reps, int) or not 1 <= reps <= 20:
        raise ForgeError("suite.reps must be an integer from 1 to 20")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ForgeError("suite.cases must be a non-empty array")
    seen: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        field = f"suite.cases[{index}]"
        case = _require_mapping(raw_case, field)
        _reject_unknown(
            case,
            {"id", "source", "plane", "category", "critical", "prompt", "fixture", "expectations"},
            field,
        )
        case_id = _require_text(case.get("id"), f"{field}.id")
        if case_id in seen:
            raise ForgeError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        source = case.get("source")
        if source not in ALLOWED_SOURCES:
            raise ForgeError(f"{field}.source must be one of {sorted(ALLOWED_SOURCES)}")
        plane = case.get("plane")
        if plane not in ALLOWED_PLANES:
            raise ForgeError(f"{field}.plane must be one of {sorted(ALLOWED_PLANES)}")
        category = case.get("category")
        if category not in ALLOWED_CATEGORIES:
            raise ForgeError(f"{field}.category must be one of {sorted(ALLOWED_CATEGORIES)}")
        if not isinstance(case.get("critical"), bool):
            raise ForgeError(f"{field}.critical must be boolean")
        prompt = _require_text(case.get("prompt"), f"{field}.prompt")
        fixture = case.get("fixture")
        if fixture is not None:
            fixture = require_relative_path(fixture, f"{field}.fixture")
        expectations = case.get("expectations")
        if not isinstance(expectations, list) or not expectations:
            raise ForgeError(f"{field}.expectations must be a non-empty array")
        normalized_expectations: list[dict[str, Any]] = []
        kinds: list[str] = []
        for expectation_index, raw_expectation in enumerate(expectations):
            expectation_field = f"{field}.expectations[{expectation_index}]"
            expectation = _require_mapping(raw_expectation, expectation_field)
            kind = expectation.get("kind")
            if kind not in EXPECTATION_FIELDS:
                raise ForgeError(
                    f"{expectation_field}.kind must be one of {sorted(EXPECTATION_FIELDS)}"
                )
            _reject_unknown(expectation, EXPECTATION_FIELDS[kind], expectation_field)
            kinds.append(kind)
            item = dict(expectation)
            if kind == "selected_skill":
                selected = item.get("value")
                if selected is not None and not isinstance(selected, str):
                    raise ForgeError(f"{expectation_field}.value must be text or null")
            elif kind == "exit_code":
                if isinstance(item.get("value"), bool) or not isinstance(item.get("value"), int):
                    raise ForgeError(f"{expectation_field}.value must be an integer")
            elif kind == "stdout_equals":
                if not isinstance(item.get("value"), str):
                    raise ForgeError(f"{expectation_field}.value must be text")
            elif kind in {"stdout_contains", "stdout_not_contains"}:
                _require_text(item.get("value"), f"{expectation_field}.value")
            elif kind in {"file_exists", "file_not_exists"}:
                item["path"] = require_relative_path(item.get("path"), f"{expectation_field}.path")
            elif kind == "file_sha256":
                item["path"] = require_relative_path(item.get("path"), f"{expectation_field}.path")
                digest = item.get("value")
                if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
                    raise ForgeError(f"{expectation_field}.value must be a sha256 digest")
            elif kind == "json_equals":
                item["path"] = require_relative_path(item.get("path"), f"{expectation_field}.path")
                if "value" not in item:
                    raise ForgeError(f"{expectation_field}.value is required")
                canonical_json_bytes(item["value"])
            elif kind == "validator":
                item["path"] = require_relative_path(item.get("path"), f"{expectation_field}.path")
                args = item.get("args", [])
                if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                    raise ForgeError(f"{expectation_field}.args must be an array of strings")
                timeout = item.get("timeout_seconds", 10)
                if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
                    raise ForgeError(
                        f"{expectation_field}.timeout_seconds must be from 1 to 120"
                    )
                item["args"] = args
                item["timeout_seconds"] = timeout
            normalized_expectations.append(item)
        if plane == "routing":
            if kinds != ["selected_skill"]:
                raise ForgeError(f"{field} routing cases require exactly one selected_skill expectation")
            if re.search(
                rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])",
                prompt.lower(),
            ):
                raise ForgeError(f"{field} routing prompt must not name the Skill")
        elif "selected_skill" in kinds:
            raise ForgeError(f"{field} execution cases cannot use selected_skill")
        if case["critical"] and plane == "execution" and not (
            set(kinds) & STRONG_EXECUTION_EXPECTATIONS
        ):
            raise ForgeError(
                f"{field} critical execution case needs a strong deterministic expectation"
            )
        normalized_cases.append(
            {
                "id": case_id,
                "source": source,
                "plane": plane,
                "category": category,
                "critical": case["critical"],
                "prompt": prompt,
                "fixture": fixture,
                "expectations": normalized_expectations,
            }
        )
    return {
        "version": 1,
        "skill": skill,
        "mode": mode,
        "reps": reps,
        "cases": normalized_cases,
    }


def load_suite(path: Path) -> dict[str, Any]:
    return validate_suite(read_json_strict(path))


def validate_sealed_pairing(
    visible: dict[str, Any], sealed: dict[str, Any]
) -> None:
    """Check that a sealed suite is a disjoint extension of the visible one.

    A sealed suite is withheld from the agent that builds the candidate, so it
    must target the same Skill and mode while adding genuinely new cases.
    """
    if sealed["skill"] != visible["skill"]:
        raise ForgeError(
            f"sealed suite targets {sealed['skill']!r}, visible targets {visible['skill']!r}"
        )
    if sealed["mode"] != visible["mode"]:
        raise ForgeError(
            f"sealed suite mode {sealed['mode']!r} differs from visible mode {visible['mode']!r}"
        )
    if sealed["reps"] != visible["reps"]:
        raise ForgeError("sealed suite reps must equal visible suite reps")
    overlap = sorted(
        {case["id"] for case in sealed["cases"]}
        & {case["id"] for case in visible["cases"]}
    )
    if overlap:
        raise ForgeError(
            f"sealed and visible suites share case ids: {', '.join(overlap)}"
        )
    if not sealed["cases"]:
        raise ForgeError("sealed suite must contain at least one case")


def render_skill_bundle(root: Path) -> str:
    root = root.expanduser().resolve(strict=True)
    parts: list[str] = []
    total = 0
    for entry in inspect_tree(root, reject_unsafe=True):
        if entry.kind != "file":
            continue
        relative = PurePosixPath(entry.path)
        if relative.parts[0] not in {"SKILL.md", "references", "scripts"}:
            continue
        path = root / entry.path
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        encoded = content.encode("utf-8")
        total += len(encoded)
        if total > 500_000:
            raise ForgeError("rendered Skill bundle exceeds 500000 bytes")
        parts.append(f"--- FILE: {entry.path} ---\n{content.rstrip()}\n")
    if not any(part.startswith("--- FILE: SKILL.md ---") for part in parts):
        raise ForgeError("Skill bundle is missing SKILL.md")
    return "\n".join(parts)


def minimal_environment(extra_names: Iterable[str] = ()) -> dict[str, str]:
    names = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        *extra_names,
    }
    result = {name: os.environ[name] for name in names if name in os.environ}
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result
