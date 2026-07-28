#!/usr/bin/env python3
"""Reduce raw Skill Forge observations into case results and bounded claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from forge_core import (
    ForgeError,
    digest_file,
    digest_json,
    load_suite,
    loads_json_strict,
    read_json_strict,
    resolve_under,
    validate_sealed_pairing,
    write_json_atomic,
)


CASE_STATUSES = frozenset({"passed", "failed", "inconclusive", "not_run"})

# A fixture host replays a frozen response, so every expectation is objective.
# A live host only exposes two channels: free-form assistant text and the bytes
# it left in the workspace. Artifact expectations are objective there; weak text
# matches are indicative only; exact text, process exit status, and route
# telemetry are not observable at all and must never decide a case.
LIVE_OBJECTIVE_KINDS = frozenset(
    {"file_exists", "file_not_exists", "file_sha256", "json_equals", "validator"}
)
LIVE_INDICATIVE_KINDS = frozenset({"stdout_contains", "stdout_not_contains"})
UNOBSERVABLE_REASONS = {
    "stdout_equals": "a live host returns assistant prose, not exact task stdout",
    "exit_code": "a live host exit status describes the CLI, not the task",
    "selected_skill": "no live host exposes route telemetry",
}


def _observability(kind: str, host: str) -> str:
    if host == "fixture":
        return "objective"
    if kind in LIVE_OBJECTIVE_KINDS:
        return "objective"
    if kind in LIVE_INDICATIVE_KINDS:
        return "indicative"
    return "unobservable"


def _load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_dir = run_dir.expanduser().resolve(strict=True)
    manifest_path = run_dir / "manifest.json"
    results_path = run_dir / "results.jsonl"
    manifest = read_json_strict(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ForgeError(f"invalid run manifest: {manifest_path}")
    if manifest.get("status") != "completed":
        raise ForgeError(f"run is not complete: {run_dir}")
    if digest_file(results_path) != manifest.get("results_digest"):
        raise ForgeError(f"results digest mismatch: {run_dir}")
    records: list[dict[str, Any]] = []
    with results_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = loads_json_strict(line)
            if not isinstance(value, dict):
                raise ForgeError(f"results line {line_number} is not an object: {run_dir}")
            if value.get("run_id") != manifest.get("run_id"):
                raise ForgeError(f"results line {line_number} has the wrong run_id")
            records.append(value)
    if len(records) != manifest.get("result_count"):
        raise ForgeError(f"result count mismatch: {run_dir}")
    return manifest, records


def _read_raw(run_dir: Path, info: Any, field: str) -> str:
    if not isinstance(info, dict) or not isinstance(info.get("path"), str):
        raise ForgeError(f"observation {field} is missing a raw path")
    path = resolve_under(run_dir, info["path"], must_exist=True)
    if digest_file(path) != info.get("digest"):
        raise ForgeError(f"raw {field} digest mismatch: {path}")
    return path.read_text(encoding="utf-8")


def _evaluate_expectation(
    expectation: dict[str, Any],
    record: dict[str, Any],
    run_dir: Path,
    stdout: str,
) -> tuple[str, str]:
    kind = expectation["kind"]
    if kind == "selected_skill":
        return (
            ("passed", "selected_skill matched")
            if record.get("selected_skill") == expectation.get("value")
            else ("failed", "selected_skill differed")
        )
    if kind == "exit_code":
        return (
            ("passed", "exit_code matched")
            if record.get("exit_code") == expectation["value"]
            else ("failed", "exit_code differed")
        )
    if kind == "stdout_equals":
        return (
            ("passed", "stdout matched exactly")
            if stdout == expectation["value"]
            else ("failed", "stdout differed")
        )
    if kind == "stdout_contains":
        return (
            ("passed", "stdout contained text")
            if expectation["value"] in stdout
            else ("failed", "stdout did not contain text")
        )
    if kind == "stdout_not_contains":
        return (
            ("passed", "stdout excluded text")
            if expectation["value"] not in stdout
            else ("failed", "stdout contained forbidden text")
        )
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        return "inconclusive", "artifact observation is missing"
    if kind == "file_exists":
        return (
            ("passed", "file exists")
            if expectation["path"] in artifacts
            else ("failed", "file is absent")
        )
    if kind == "file_not_exists":
        return (
            ("passed", "file is absent")
            if expectation["path"] not in artifacts
            else ("failed", "file exists")
        )
    if kind == "file_sha256":
        observed = artifacts.get(expectation["path"])
        if not isinstance(observed, dict):
            return "failed", "file is absent"
        return (
            ("passed", "file digest matched")
            if observed.get("digest") == expectation["value"]
            else ("failed", "file digest differed")
        )
    if kind == "json_equals":
        workspace = record.get("workspace")
        if not isinstance(workspace, str):
            return "inconclusive", "workspace observation is missing"
        try:
            workspace_root = resolve_under(run_dir, workspace, must_exist=True)
            artifact_path = resolve_under(workspace_root, expectation["path"], must_exist=True)
            observed = read_json_strict(artifact_path)
        except (ForgeError, FileNotFoundError, NotADirectoryError):
            return "failed", "JSON artifact is absent or invalid"
        return (
            ("passed", "JSON value matched")
            if observed == expectation["value"]
            else ("failed", "JSON value differed")
        )
    if kind == "validator":
        validators = record.get("validators")
        if not isinstance(validators, list):
            return "inconclusive", "validator observations are missing"
        matches = [item for item in validators if item.get("path") == expectation["path"]]
        if len(matches) != 1:
            return "inconclusive", "validator observation is not unique"
        match = matches[0]
        if match.get("status") == "not_run":
            return "not_run", "validator was not run"
        if match.get("status") != "completed":
            return "inconclusive", "validator did not complete cleanly"
        return (
            ("passed", "validator exited zero")
            if match.get("exit_code") == 0
            else ("failed", "validator exited non-zero")
        )
    raise ForgeError(f"unsupported expectation kind: {kind}")


def _evaluate_record(
    case: dict[str, Any], record: dict[str, Any], run_dir: Path, host: str
) -> dict[str, Any]:
    status = record.get("status")
    if status == "not_run":
        return {"status": "not_run", "reason": record.get("reason"), "checks": []}
    if status in {"infra_error", "integrity_error"}:
        return {"status": "inconclusive", "reason": record.get("reason"), "checks": []}
    if status != "completed":
        return {"status": "inconclusive", "reason": "unknown run status", "checks": []}
    stdout = _read_raw(run_dir, record.get("stdout"), "stdout")
    checks: list[dict[str, Any]] = []
    statuses: list[str] = []
    for expectation in case["expectations"]:
        kind = expectation["kind"]
        observability = _observability(kind, host)
        if observability == "unobservable":
            checks.append(
                {
                    "kind": kind,
                    "status": "not_run",
                    "observability": observability,
                    "reason": UNOBSERVABLE_REASONS.get(kind, "not observable on this host"),
                }
            )
            statuses.append("not_run")
            continue
        check_status, reason = _evaluate_expectation(
            expectation, record, run_dir, stdout
        )
        checks.append(
            {
                "kind": kind,
                "status": check_status,
                "observability": observability,
                "reason": reason,
            }
        )
        # An indicative check is recorded but never decides the case.
        if observability == "objective":
            statuses.append(check_status)
    if not statuses:
        result = "not_run"
    elif "failed" in statuses:
        result = "failed"
    elif "inconclusive" in statuses:
        result = "inconclusive"
    elif "not_run" in statuses:
        result = "not_run"
    else:
        result = "passed"
    return {"status": result, "reason": None, "checks": checks}


def _aggregate_reps(rep_results: list[dict[str, Any]]) -> str:
    statuses = [result["status"] for result in rep_results]
    if "failed" in statuses:
        return "failed"
    if "inconclusive" in statuses:
        return "inconclusive"
    if "not_run" in statuses:
        return "not_run"
    return "passed"


def _configuration_results(
    suite: dict[str, Any],
    run_dir: Path,
    records: list[dict[str, Any]],
    host: str,
) -> tuple[dict[str, Any], bool]:
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    duplicate = False
    for record in records:
        key = (record.get("case_id"), record.get("rep"))
        if key in indexed:
            duplicate = True
        indexed[key] = record
    case_results: dict[str, Any] = {}
    complete = not duplicate
    for case in suite["cases"]:
        reps: list[dict[str, Any]] = []
        for rep in range(1, suite["reps"] + 1):
            record = indexed.get((case["id"], rep))
            if record is None:
                complete = False
                reps.append(
                    {"rep": rep, "status": "inconclusive", "reason": "missing observation", "checks": []}
                )
            else:
                result = _evaluate_record(case, record, run_dir, host)
                result["rep"] = rep
                reps.append(result)
        case_results[case["id"]] = {
            "status": _aggregate_reps(reps),
            "repetitions": reps,
        }
    expected = len(suite["cases"]) * suite["reps"]
    if len(records) != expected:
        complete = False
    return case_results, complete


def _critical_state(suite: dict[str, Any], results: dict[str, Any]) -> tuple[bool, bool, bool]:
    statuses = [
        results[case["id"]]["status"] for case in suite["cases"] if case["critical"]
    ]
    failed = "failed" in statuses
    incomplete = any(status in {"inconclusive", "not_run"} for status in statuses)
    passed = bool(statuses) and all(status == "passed" for status in statuses)
    return passed, failed, incomplete


def _decide(
    suite: dict[str, Any],
    by_configuration: dict[str, dict[str, Any]],
    complete: dict[str, bool],
) -> tuple[str, dict[str, Any]]:
    mode = suite["mode"]
    comparison = {"improvements": [], "regressions": [], "unchanged": []}
    if mode == "no_skill":
        results = by_configuration.get("no_skill")
        if results is None or not complete.get("no_skill", False):
            return "inconclusive", comparison
        passed, failed, incomplete = _critical_state(suite, results)
        if incomplete:
            return "inconclusive", comparison
        if failed:
            return "no_skill_not_supported_for_selected_cases", comparison
        return (
            "no_skill_supported_for_selected_cases" if passed else "inconclusive",
            comparison,
        )
    candidate = by_configuration.get("candidate")
    if candidate is None or not complete.get("candidate", False):
        return "inconclusive", comparison
    candidate_passed, candidate_failed, candidate_incomplete = _critical_state(suite, candidate)
    if candidate_incomplete:
        return "inconclusive", comparison
    if mode == "create" and "baseline" not in by_configuration and "no_skill" not in by_configuration:
        return ("reject_candidate" if candidate_failed else "handoff_candidate"), comparison
    baseline_name = "baseline" if "baseline" in by_configuration else "no_skill"
    baseline = by_configuration.get(baseline_name)
    if baseline is None or not complete.get(baseline_name, False):
        return "inconclusive", comparison
    if any(
        results[case["id"]]["status"] in {"inconclusive", "not_run"}
        for results in (baseline, candidate)
        for case in suite["cases"]
    ):
        return "inconclusive", comparison
    for case in suite["cases"]:
        case_id = case["id"]
        old = baseline[case_id]["status"]
        new = candidate[case_id]["status"]
        if old == "failed" and new == "passed":
            comparison["improvements"].append(case_id)
        elif old == "passed" and new != "passed":
            comparison["regressions"].append(case_id)
        else:
            comparison["unchanged"].append(case_id)
    critical_regression = any(
        case["id"] in comparison["regressions"] and case["critical"] for case in suite["cases"]
    )
    if candidate_failed or critical_regression:
        return ("keep_baseline" if mode == "optimize" else "reject_candidate"), comparison
    if comparison["improvements"] and candidate_passed:
        return "adopt_candidate_for_selected_cases", comparison
    return ("keep_baseline" if mode == "optimize" else "handoff_candidate"), comparison


def _claims(
    suite: dict[str, Any],
    decision: str,
    manifests: dict[str, dict[str, Any]],
    by_configuration: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    fixture_only = all(manifest.get("host") == "fixture" for manifest in manifests.values())
    proven: list[str] = []
    disproven: list[str] = []
    if fixture_only:
        proven.append("fixture_pipeline_completed_on_selected_cases")
    elif decision in {"handoff_candidate", "adopt_candidate_for_selected_cases"}:
        proven.append("candidate_passes_selected_cases")
    elif decision == "no_skill_supported_for_selected_cases":
        proven.append("no_skill_baseline_passes_selected_cases")
    if decision in {"reject_candidate", "keep_baseline"}:
        disproven.append("candidate_gain_on_selected_cases")
    if decision == "no_skill_not_supported_for_selected_cases":
        disproven.append("no_skill_sufficiency_on_selected_cases")
    routing_statuses = [
        by_configuration[configuration][case["id"]]["status"]
        for configuration in by_configuration
        for case in suite["cases"]
        if case["plane"] == "routing"
    ]
    not_run = []
    if not routing_statuses or any(status == "not_run" for status in routing_statuses) or fixture_only:
        not_run.append("automatic_routing")
    return {
        "proven": proven,
        "disproven": disproven,
        "unverified": [
            "generalization_beyond_selected_cases",
            "long_term_stability",
            "runtime_safety",
            "host_activation",
        ],
        "not_run": not_run,
    }


def _checks_by_observability(
    by_configuration: dict[str, dict[str, Any]], wanted: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for configuration in sorted(by_configuration):
        for case_id in sorted(by_configuration[configuration]):
            for repetition in by_configuration[configuration][case_id]["repetitions"]:
                for check in repetition.get("checks", []):
                    if check.get("observability") != wanted:
                        continue
                    rows.append(
                        {
                            "configuration": configuration,
                            "case_id": case_id,
                            "rep": repetition["rep"],
                            "kind": check["kind"],
                            "status": check["status"],
                            "reason": check.get("reason"),
                        }
                    )
    return rows


def _score_track(
    suite: dict[str, Any], run_dirs: list[Path], track: str
) -> dict[str, Any]:
    suite_digest = digest_json(suite)
    manifests: dict[str, dict[str, Any]] = {}
    by_configuration: dict[str, dict[str, Any]] = {}
    complete: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    for run_dir_input in run_dirs:
        run_dir = run_dir_input.expanduser().resolve(strict=True)
        manifest, records = _load_run(run_dir)
        if manifest.get("suite_digest") != suite_digest:
            raise ForgeError(f"run suite digest does not match its supplied suite: {run_dir}")
        if manifest.get("track", "visible") != track:
            raise ForgeError(
                f"run track {manifest.get('track', 'visible')!r} does not match {track!r}: {run_dir}"
            )
        configuration = manifest.get("configuration")
        if configuration in manifests:
            raise ForgeError(f"multiple {track} runs supplied for configuration: {configuration}")
        if configuration not in {"candidate", "baseline", "no_skill"}:
            raise ForgeError(f"unknown run configuration: {configuration}")
        host = manifest.get("host")
        if host not in {"fixture", "codex", "claude"}:
            raise ForgeError(f"unknown run host: {host}")
        results, matrix_complete = _configuration_results(
            suite, run_dir, records, host
        )
        manifests[configuration] = manifest
        by_configuration[configuration] = results
        complete[configuration] = matrix_complete
        evidence[configuration] = str(run_dir)
    decision, comparison = _decide(suite, by_configuration, complete)
    return {
        "suite_digest": suite_digest,
        "manifests": manifests,
        "by_configuration": by_configuration,
        "complete": complete,
        "evidence": evidence,
        "decision": decision,
        "comparison": comparison,
    }


def _case_rows(
    suite: dict[str, Any], by_configuration: dict[str, dict[str, Any]], track: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in suite["cases"]:
        rows.append(
            {
                "id": case["id"],
                "track": track,
                "plane": case["plane"],
                "critical": case["critical"],
                "source": case["source"],
                "configurations": {
                    configuration: results[case["id"]]["status"]
                    for configuration, results in by_configuration.items()
                },
            }
        )
    return rows


# Sealed cases are held out from the build agent, so they are the only evidence
# that speaks to behavior beyond what the candidate was written against. They
# never overturn a visible decision on their own; they cap or extend its claims.
SEALED_OUTCOMES = {
    "confirms": "sealed_cases_agree_with_the_visible_decision",
    "contradicts": "sealed_cases_contradict_the_visible_decision",
    "inconclusive": "sealed_cases_did_not_resolve",
}


def _sealed_verdict(visible: dict[str, Any], sealed: dict[str, Any]) -> str:
    if sealed["decision"] == "inconclusive":
        return "inconclusive"
    positive = {
        "handoff_candidate",
        "adopt_candidate_for_selected_cases",
        "no_skill_supported_for_selected_cases",
    }
    visible_positive = visible["decision"] in positive
    sealed_positive = sealed["decision"] in positive
    return "confirms" if visible_positive == sealed_positive else "contradicts"


def build_report(
    suite_path: Path,
    run_dirs: list[Path],
    sealed_suite_path: Path | None = None,
    sealed_run_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    suite_path = suite_path.expanduser().resolve(strict=True)
    suite = load_suite(suite_path)
    visible = _score_track(suite, run_dirs, "visible")
    suite_digest = visible["suite_digest"]
    manifests = visible["manifests"]
    by_configuration = visible["by_configuration"]
    complete = visible["complete"]
    evidence = visible["evidence"]
    decision, comparison = visible["decision"], visible["comparison"]
    sealed_section: dict[str, Any] | None = None
    if sealed_suite_path is not None:
        sealed_suite = load_suite(sealed_suite_path.expanduser().resolve(strict=True))
        validate_sealed_pairing(suite, sealed_suite)
        sealed = _score_track(sealed_suite, sealed_run_dirs or [], "sealed")
        verdict = _sealed_verdict(visible, sealed)
        sealed_section = {
            "suite_digest": sealed["suite_digest"],
            "runs": sealed["evidence"],
            "matrix_complete": sealed["complete"],
            "cases": _case_rows(sealed_suite, sealed["by_configuration"], "sealed"),
            "comparison": sealed["comparison"],
            "decision": sealed["decision"],
            "verdict": verdict,
            "verdict_meaning": SEALED_OUTCOMES[verdict],
            "authority": "held_out_evidence_caps_claims_and_never_replaces_the_visible_decision",
        }
    elif sealed_run_dirs:
        raise ForgeError("sealed runs require --sealed-suite")
    case_rows = _case_rows(suite, by_configuration, "visible")
    claims = _claims(suite, decision, manifests, by_configuration)
    if sealed_section is None:
        claims["unverified"].append("behavior_on_held_out_cases")
    elif sealed_section["verdict"] == "confirms":
        claims["proven"].append("visible_decision_reproduced_on_held_out_cases")
    elif sealed_section["verdict"] == "contradicts":
        claims["disproven"].append("visible_decision_reproduced_on_held_out_cases")
    else:
        claims["not_run"].append("held_out_case_confirmation")
    return {
        "version": 1,
        "suite_digest": suite_digest,
        "mode": suite["mode"],
        "skill": suite["skill"],
        "runs": evidence,
        "identities": {
            configuration: {
                "skill_digest": manifest.get("skill_digest"),
                "host": manifest.get("host"),
                "host_version": manifest.get("host_probe", {}).get("version"),
                "claim_cap": manifest.get("claim_cap"),
            }
            for configuration, manifest in manifests.items()
        },
        "matrix_complete": complete,
        "cases": case_rows,
        "comparison": comparison,
        "claims": claims,
        "indicative_observations": _checks_by_observability(
            by_configuration, "indicative"
        ),
        "unobservable_checks": _checks_by_observability(
            by_configuration, "unobservable"
        ),
        "sealed": sealed_section,
        "decision": decision,
        "limitations": [
            "selected_cases_only",
            "not_installed",
            "not_released",
            *(
                ["fixture_host_only"]
                if manifests and all(item.get("host") == "fixture" for item in manifests.values())
                else []
            ),
            *([] if sealed_section else ["no_held_out_cases"]),
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Skill Forge Report",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "| Case | Plane | Critical | Results |",
        "|---|---|---:|---|",
    ]
    for case in report["cases"]:
        results = ", ".join(
            f"{configuration}={status}"
            for configuration, status in sorted(case["configurations"].items())
        )
        lines.append(
            f"| {case['id']} | {case['plane']} | {'yes' if case['critical'] else 'no'} | {results} |"
        )
    sealed = report.get("sealed")
    if sealed:
        lines.extend(
            [
                "",
                "## Held-out cases",
                "",
                f"Sealed decision: `{sealed['decision']}` ({sealed['verdict_meaning']})",
                "",
                "| Case | Plane | Critical | Results |",
                "|---|---|---:|---|",
            ]
        )
        for case in sealed["cases"]:
            results = ", ".join(
                f"{configuration}={status}"
                for configuration, status in sorted(case["configurations"].items())
            )
            lines.append(
                f"| {case['id']} | {case['plane']} | {'yes' if case['critical'] else 'no'} | {results} |"
            )
        lines.extend(
            [
                "",
                "These cases were withheld from the agent that built the candidate. They cap or extend the claims above; they never replace the decision.",
            ]
        )
    unobservable = report.get("unobservable_checks", [])
    if unobservable:
        lines.extend(["", "## Not observable on this host", ""])
        for item in unobservable:
            lines.append(
                f"- `{item['case_id']}` / `{item['kind']}`: {item['reason']}"
            )
    indicative = report.get("indicative_observations", [])
    if indicative:
        lines.extend(
            ["", "## Indicative observations (do not decide any case)", ""]
        )
        for item in indicative:
            lines.append(
                f"- `{item['case_id']}` / `{item['kind']}`: {item['status']}"
            )
    lines.extend(["", "## Claims", ""])
    labels = {
        "proven": "Proven",
        "disproven": "Disproven",
        "unverified": "Unverified",
        "not_run": "Not run",
    }
    for key in ("proven", "disproven", "unverified", "not_run"):
        values = report["claims"][key]
        lines.append(f"- {labels[key]}: {', '.join(values) if values else 'none'}")
    lines.extend(
        [
            "",
            "This report is limited to the frozen selected cases. It does not prove installation, release, host activation, or general runtime safety.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument(
        "--sealed-suite",
        type=Path,
        help="suite withheld from the build agent; scored as held-out evidence",
    )
    parser.add_argument("--sealed-run", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output_dir = args.output_dir.expanduser().resolve(strict=False)
        if output_dir.exists() or output_dir.is_symlink():
            raise ForgeError(f"output directory already exists: {output_dir}")
        report = build_report(
            args.suite, args.run, args.sealed_suite, args.sealed_run
        )
        output_dir.mkdir(parents=True)
        write_json_atomic(output_dir / "report.json", report)
        (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    except (ForgeError, FileNotFoundError, NotADirectoryError, OSError) as exc:
        print(f"skill-forge score failed: {exc}", file=sys.stderr)
        return 2
    print(output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
