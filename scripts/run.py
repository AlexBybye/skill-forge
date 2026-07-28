#!/usr/bin/env python3
"""Run a frozen Skill Forge suite and record raw observations only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid

from forge_core import (
    ForgeError,
    digest_bytes,
    digest_file,
    digest_json,
    load_suite,
    minimal_environment,
    read_json_strict,
    render_skill_bundle,
    require_relative_path,
    resolve_under,
    snapshot_tree,
    tree_digest,
    write_json_atomic,
)


MAX_CAPTURE_BYTES = 1_000_000
CONFIGURATIONS = frozenset({"candidate", "baseline", "no_skill"})
# A visible suite is given to the agent that builds the candidate. A sealed
# suite is withheld from it, so sealed results are held out by construction.
TRACKS = frozenset({"visible", "sealed"})
HOSTS = frozenset({"fixture", "codex", "claude"})
POLICIES = frozenset({"read-only", "workspace-write"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_CAPTURE_BYTES:
        return value, False
    truncated = encoded[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")
    return truncated, True


def _write_raw(path: Path, value: str) -> dict[str, Any]:
    value, truncated = _bounded_text(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return {
        "path": path.as_posix(),
        "digest": digest_file(path),
        "bytes": path.stat().st_size,
        "truncated": truncated,
    }


def _artifact_map(workspace: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(workspace.rglob("*")):
        if ".skill-under-test" in path.parts or path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        result[relative] = {
            "digest": digest_file(path),
            "bytes": path.stat().st_size,
        }
    return result


def _artifact_delta(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "created": sorted(after_paths - before_paths),
        "changed": sorted(
            path
            for path in before_paths & after_paths
            if before[path]["digest"] != after[path]["digest"]
        ),
        "deleted": sorted(before_paths - after_paths),
    }


def _copy_fixture_input(fixture: Path | None, workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=False)
    if fixture is None:
        return
    input_root = fixture / "input"
    if not input_root.exists():
        return
    if not input_root.is_dir() or input_root.is_symlink():
        raise ForgeError(f"fixture input must be a regular directory: {input_root}")
    for item in sorted(input_root.iterdir()):
        target = workspace / item.name
        if item.is_symlink():
            raise ForgeError(f"fixture input contains a symlink: {item}")
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, target)
        else:
            raise ForgeError(f"fixture input contains a special file: {item}")


def _fixture_host(
    fixture: Path | None,
    configuration: str,
    workspace: Path,
) -> dict[str, Any]:
    if fixture is None:
        raise ForgeError("fixture host requires case.fixture")
    response_path = fixture / f"response.{configuration}.json"
    if not response_path.is_file():
        return {
            "status": "not_run",
            "reason": f"fixture response missing: {response_path.name}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "selected_skill": None,
            "argv": [],
            "transport_stdout": "",
        }
    value = read_json_strict(response_path)
    if not isinstance(value, dict):
        raise ForgeError(f"fixture response must be an object: {response_path}")
    unknown = sorted(set(value) - {"stdout", "stderr", "exit_code", "selected_skill", "artifacts"})
    if unknown:
        raise ForgeError(f"fixture response has unknown fields: {', '.join(unknown)}")
    stdout = value.get("stdout", "")
    stderr = value.get("stderr", "")
    exit_code = value.get("exit_code", 0)
    selected = value.get("selected_skill")
    artifacts = value.get("artifacts", {})
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ForgeError("fixture stdout and stderr must be strings")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ForgeError("fixture exit_code must be an integer")
    if selected is not None and not isinstance(selected, str):
        raise ForgeError("fixture selected_skill must be text or null")
    if not isinstance(artifacts, dict):
        raise ForgeError("fixture artifacts must be an object")
    for relative, content in artifacts.items():
        safe = require_relative_path(relative, "fixture artifact path")
        if not isinstance(content, str):
            raise ForgeError(f"fixture artifact content must be text: {safe}")
        target = resolve_under(workspace, safe)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {
        "status": "completed",
        "reason": None,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "selected_skill": selected,
        "argv": ["fixture", response_path.name],
        "transport_stdout": "",
    }


def _run_probe(argv: list[str]) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=minimal_environment(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return completed.returncode, completed.stdout + completed.stderr


def _probe_host(host: str, executable: str) -> dict[str, Any]:
    if host == "fixture":
        return {"status": "supported", "version": "fixture/v1", "help_digest": None}
    code, version = _run_probe([executable, "--version"])
    version_line = next((line.strip() for line in version.splitlines() if line.strip()), "")
    if code != 0:
        return {"status": "unavailable", "version": version_line, "help_digest": None}
    help_commands = [[executable, "--help"]]
    if host == "codex":
        help_commands.append([executable, "exec", "--help"])
    help_text = ""
    for command in help_commands:
        help_code, text = _run_probe(command)
        if help_code != 0:
            return {
                "status": "unavailable",
                "version": version_line,
                "help_digest": digest_bytes(help_text.encode("utf-8")) if help_text else None,
            }
        help_text += text
    required = (
        ["exec", "--json", "--ephemeral", "--sandbox", "--output-last-message"]
        if host == "codex"
        else ["--print", "--output-format", "--permission-mode", "--tools"]
    )
    missing = [flag for flag in required if flag not in help_text]
    return {
        "status": "supported" if not missing else "unavailable",
        "version": version_line,
        "help_digest": digest_bytes(help_text.encode("utf-8")),
        "missing_flags": missing,
    }


def _render_prompt(case: dict[str, Any], bundle: str | None, policy: str) -> str:
    parts = [
        "You are executing one frozen Skill evaluation case.",
        "Do not claim that the Skill was automatically routed; this is explicit execution.",
    ]
    if bundle is not None:
        parts.extend(
            [
                "Apply the following Skill bundle to the task.",
                "<skill_bundle>",
                bundle,
                "</skill_bundle>",
            ]
        )
    else:
        parts.append("No candidate Skill is enabled for this baseline probe.")
    parts.append(f"Execution policy: {policy}.")
    parts.append("<task>")
    parts.append(case["prompt"])
    parts.append("</task>")
    return "\n".join(parts) + "\n"


def _host_failure(
    host: str, exit_code: int | None, transport_stdout: str
) -> str | None:
    """Detect a host transport failure, which is never candidate evidence."""
    if exit_code is None:
        return "host produced no exit status"
    if exit_code != 0:
        return f"host exited {exit_code}"
    if host == "codex":
        for line in transport_stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "turn.failed":
                error = event.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                return f"codex turn failed: {message or 'unknown error'}"
    elif host == "claude":
        try:
            parsed = json.loads(transport_stdout)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed.get("is_error") is True:
            subtype = parsed.get("subtype")
            return f"claude reported an error: {subtype or 'unknown error'}"
    return None


def _live_host(
    host: str,
    executable: str,
    model: str | None,
    policy: str,
    workspace: Path,
    raw_root: Path,
    prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    response_path = raw_root / "last-message.txt"
    if host == "codex":
        argv = [
            executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--ephemeral",
            "--sandbox",
            policy,
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            str(response_path),
            "-C",
            str(workspace),
        ]
        if model:
            argv.extend(["--model", model])
        argv.append("-")
    elif host == "claude":
        if policy != "read-only":
            return {
                "status": "not_run",
                "reason": "claude workspace-write policy is not implemented",
                "stdout": "",
                "stderr": "",
                "exit_code": None,
                "selected_skill": None,
                "argv": [],
                "transport_stdout": "",
            }
        argv = [
            executable,
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--tools",
            "",
            "--no-session-persistence",
            "--add-dir",
            str(workspace),
        ]
        if model:
            argv.extend(["--model", model])
    else:
        raise ForgeError(f"unsupported live host: {host}")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            input=prompt,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=workspace,
            env=minimal_environment(
                {"ANTHROPIC_API_KEY"} if host == "claude" else set()
            ),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "infra_error",
            "reason": f"host timed out after {timeout_seconds}s",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "exit_code": None,
            "selected_skill": None,
            "argv": argv,
            "duration_seconds": time.monotonic() - started,
            "transport_stdout": exc.stdout or "",
        }
    except OSError as exc:
        return {
            "status": "infra_error",
            "reason": str(exc),
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "selected_skill": None,
            "argv": argv,
            "duration_seconds": time.monotonic() - started,
            "transport_stdout": "",
        }
    stdout = completed.stdout
    if host == "codex" and response_path.is_file():
        stdout = response_path.read_text(encoding="utf-8")
    elif host == "claude":
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
                stdout = parsed["result"]
        except json.JSONDecodeError:
            pass
    failure = _host_failure(host, completed.returncode, completed.stdout)
    return {
        "status": "infra_error" if failure else "completed",
        "reason": failure,
        "stdout": stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
        "selected_skill": None,
        "argv": argv,
        "duration_seconds": time.monotonic() - started,
        "transport_stdout": completed.stdout,
    }


def _run_validators(
    case: dict[str, Any],
    suite_root: Path,
    workspace: Path,
    allowed: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    observations: list[dict[str, Any]] = []
    drifted = False
    for expectation in case["expectations"]:
        if expectation["kind"] != "validator":
            continue
        relative = expectation["path"]
        if relative not in allowed:
            observations.append(
                {
                    "path": relative,
                    "status": "not_run",
                    "reason": "validator was not explicitly allowed",
                    "digest": None,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            )
            continue
        validator = resolve_under(suite_root, relative, must_exist=True)
        if not validator.is_file() or validator.is_symlink():
            raise ForgeError(f"validator must be a regular file: {relative}")
        argv = (
            [sys.executable, str(validator), *expectation["args"]]
            if validator.suffix == ".py"
            else [str(validator), *expectation["args"]]
        )
        before = _artifact_map(workspace)
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=expectation["timeout_seconds"],
                cwd=workspace,
                env=minimal_environment(),
            )
            status = "completed"
            reason = None
            exit_code = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            status = "infra_error"
            reason = "validator timeout"
            exit_code = None
            stdout, stderr = exc.stdout or "", exc.stderr or ""
        after = _artifact_map(workspace)
        if before != after:
            drifted = True
            status = "integrity_error"
            reason = "validator changed case artifacts"
        stdout, stdout_truncated = _bounded_text(stdout)
        stderr, stderr_truncated = _bounded_text(stderr)
        observations.append(
            {
                "path": relative,
                "status": status,
                "reason": reason,
                "digest": digest_file(validator),
                "argv": argv,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_digest": digest_bytes(stdout.encode("utf-8", errors="replace")),
                "stderr_digest": digest_bytes(stderr.encode("utf-8", errors="replace")),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        )
    return observations, drifted


def execute(args: argparse.Namespace) -> Path:
    suite_path = args.suite.expanduser().resolve(strict=True)
    suite_root = suite_path.parent
    suite = load_suite(suite_path)
    configuration = args.configuration
    if configuration not in CONFIGURATIONS:
        raise ForgeError(f"unsupported configuration: {configuration}")
    if suite["mode"] == "no_skill" and configuration != "no_skill":
        raise ForgeError("no_skill suites may run only configuration=no_skill")
    if configuration == "no_skill" and args.skill_root is not None:
        raise ForgeError("no_skill runs must not receive --skill-root")
    if configuration != "no_skill" and args.skill_root is None:
        raise ForgeError("candidate and baseline runs require --skill-root")
    if args.host not in HOSTS or args.policy not in POLICIES:
        raise ForgeError("unsupported host or execution policy")
    if args.host == "claude" and args.policy == "workspace-write":
        raise ForgeError("Claude workspace-write is not implemented in v1")
    track = getattr(args, "track", "visible") or "visible"
    if track not in TRACKS:
        raise ForgeError(f"unsupported track: {track}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    runs_dir = args.runs_dir.expanduser().resolve(strict=False)
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    suite_snapshot = run_dir / "suite.snapshot.json"
    write_json_atomic(suite_snapshot, suite)
    skill_digest: str | None = None
    skill_snapshot: Path | None = None
    bundle: str | None = None
    if args.skill_root is not None:
        source = args.skill_root.expanduser().resolve(strict=True)
        if not (source / "SKILL.md").is_file():
            raise ForgeError("skill root is missing SKILL.md")
        skill_snapshot = run_dir / "inputs" / configuration
        skill_digest = snapshot_tree(source, skill_snapshot)
        bundle = render_skill_bundle(skill_snapshot)
    executable = args.executable or ("codex" if args.host == "codex" else "claude")
    probe = _probe_host(args.host, executable) if args.host != "fixture" else _probe_host("fixture", "fixture")
    manifest: dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "suite_digest": digest_json(suite),
        "track": track,
        "configuration": configuration,
        "skill_digest": skill_digest,
        "host": args.host,
        "host_probe": probe,
        "model": args.model,
        "policy": args.policy,
        "reps": suite["reps"],
        "allowed_validators": sorted(set(args.allow_validator)),
        "claim_cap": (
            "fixture_pipeline_only" if args.host == "fixture" else "explicit_execution_only"
        ),
    }
    write_json_atomic(run_dir / "manifest.json", manifest)
    records: list[dict[str, Any]] = []
    results_path = run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as results_stream:
        for case in suite["cases"]:
            fixture = None
            if case["fixture"] is not None:
                fixture = resolve_under(suite_root, case["fixture"], must_exist=True)
                if not fixture.is_dir():
                    raise ForgeError(f"case fixture is not a directory: {case['fixture']}")
            for rep in range(1, suite["reps"] + 1):
                workspace = run_dir / "workspaces" / case["id"] / str(rep)
                _copy_fixture_input(fixture, workspace)
                if skill_snapshot is not None and args.policy == "workspace-write":
                    snapshot_tree(skill_snapshot, workspace / ".skill-under-test")
                before = _artifact_map(workspace)
                prompt = _render_prompt(case, bundle, args.policy)
                raw_root = run_dir / "raw" / case["id"] / str(rep)
                raw_root.mkdir(parents=True, exist_ok=True)
                if args.host == "fixture":
                    observation = _fixture_host(fixture, configuration, workspace)
                elif probe["status"] != "supported":
                    observation = {
                        "status": "not_run",
                        "reason": "required host flags are unavailable",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                        "selected_skill": None,
                        "argv": [],
                        "transport_stdout": "",
                    }
                elif configuration == "no_skill":
                    observation = {
                        "status": "not_run",
                        "reason": "candidate-free host Skill discovery isolation is unavailable",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                        "selected_skill": None,
                        "argv": [],
                        "transport_stdout": "",
                    }
                elif case["plane"] == "routing":
                    observation = {
                        "status": "not_run",
                        "reason": "candidate injection and route telemetry are unavailable",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                        "selected_skill": None,
                        "argv": [],
                        "transport_stdout": "",
                    }
                elif args.policy == "read-only" and any(
                    expectation["kind"]
                    in {"file_exists", "file_not_exists", "file_sha256", "json_equals", "validator"}
                    for expectation in case["expectations"]
                ):
                    observation = {
                        "status": "not_run",
                        "reason": "artifact expectations require an isolated workspace-write run",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                        "selected_skill": None,
                        "argv": [],
                        "transport_stdout": "",
                    }
                else:
                    observation = _live_host(
                        args.host,
                        executable,
                        args.model,
                        args.policy,
                        workspace,
                        raw_root,
                        prompt,
                        args.timeout_seconds,
                    )
                after_host = _artifact_map(workspace)
                validators: list[dict[str, Any]] = []
                validator_drift = False
                if observation["status"] == "completed":
                    validators, validator_drift = _run_validators(
                        case,
                        suite_root,
                        workspace,
                        set(args.allow_validator),
                    )
                if validator_drift:
                    observation["status"] = "integrity_error"
                    observation["reason"] = "validator changed artifacts"
                stdout_info = _write_raw(raw_root / "stdout.txt", observation["stdout"])
                stderr_info = _write_raw(raw_root / "stderr.txt", observation["stderr"])
                transport_info = _write_raw(
                    raw_root / "host-transport.txt",
                    observation.get("transport_stdout", ""),
                )
                stdout_info["path"] = (raw_root / "stdout.txt").relative_to(run_dir).as_posix()
                stderr_info["path"] = (raw_root / "stderr.txt").relative_to(run_dir).as_posix()
                transport_info["path"] = (
                    raw_root / "host-transport.txt"
                ).relative_to(run_dir).as_posix()
                record = {
                    "version": 1,
                    "kind": "case_observation",
                    "run_id": run_id,
                    "case_id": case["id"],
                    "rep": rep,
                    "configuration": configuration,
                    "status": observation["status"],
                    "reason": observation.get("reason"),
                    "prompt_digest": digest_bytes(prompt.encode("utf-8")),
                    "argv": observation.get("argv", []),
                    "exit_code": observation.get("exit_code"),
                    "selected_skill": observation.get("selected_skill"),
                    "stdout": stdout_info,
                    "stderr": stderr_info,
                    "host_transport": transport_info,
                    "workspace": workspace.relative_to(run_dir).as_posix(),
                    "artifacts": after_host,
                    "artifact_delta": _artifact_delta(before, after_host),
                    "validators": validators,
                    "duration_seconds": observation.get("duration_seconds"),
                }
                records.append(record)
                results_stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                results_stream.flush()
    if skill_snapshot is not None and tree_digest(skill_snapshot, reject_unsafe=True) != skill_digest:
        manifest["status"] = "integrity_error"
        manifest["failure"] = "snapshotted Skill changed during execution"
    else:
        manifest["status"] = "completed"
    manifest["ended_at"] = _utc_now()
    manifest["result_count"] = len(records)
    manifest["results_digest"] = digest_file(results_path)
    write_json_atomic(run_dir / "manifest.json", manifest)
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--configuration", choices=sorted(CONFIGURATIONS), required=True)
    parser.add_argument(
        "--track",
        choices=sorted(TRACKS),
        default="visible",
        help="visible = suite the build agent received; sealed = suite withheld from it",
    )
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--host", choices=sorted(HOSTS), default="fixture")
    parser.add_argument("--policy", choices=sorted(POLICIES), default="read-only")
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--executable")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--allow-validator", action="append", default=[])
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 1800:
        parser.error("--timeout-seconds must be from 1 to 1800")
    try:
        run_dir = execute(args)
    except (ForgeError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        print(f"skill-forge run failed: {exc}", file=sys.stderr)
        return 2
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
