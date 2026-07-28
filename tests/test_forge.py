from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check as check_module  # noqa: E402
import forge_core  # noqa: E402
import package as package_module  # noqa: E402
import run as run_module  # noqa: E402
import score as score_module  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_skill(root: Path, name: str = "test-skill", body: str = "Follow the task.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Perform a deterministic test task. Use when the test task is requested.\n"
        "---\n\n"
        f"# Test Skill\n\n{body}\n",
        encoding="utf-8",
    )
    return root


def execution_case(**updates: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "core",
        "source": "observed",
        "plane": "execution",
        "category": "core",
        "critical": True,
        "prompt": "Perform the test task.",
        "fixture": "cases/core",
        "expectations": [{"kind": "stdout_equals", "value": "ok\n"}],
    }
    case.update(updates)
    return case


def suite_value(mode: str = "create", cases: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "version": 1,
        "skill": "test-skill",
        "mode": mode,
        "reps": 1,
        "cases": cases or [execution_case()],
    }


def fixture_response(root: Path, configuration: str, **updates: object) -> None:
    value: dict[str, object] = {
        "stdout": "ok\n",
        "stderr": "",
        "exit_code": 0,
        "selected_skill": None,
        "artifacts": {},
    }
    value.update(updates)
    write_json(root / "cases" / "core" / f"response.{configuration}.json", value)


def run_fixture(
    suite: Path,
    runs_dir: Path,
    configuration: str,
    skill_root: Path | None,
    allowed: list[str] | None = None,
    track: str = "visible",
) -> Path:
    return run_module.execute(
        argparse.Namespace(
            suite=suite,
            configuration=configuration,
            skill_root=skill_root,
            host="fixture",
            policy="read-only",
            runs_dir=runs_dir,
            model=None,
            executable=None,
            timeout_seconds=30,
            allow_validator=allowed or [],
            track=track,
        )
    )


# A stub that satisfies the Codex probe, then fails the turn exactly the way a
# real network interruption does. Used to prove transport failure is never
# scored as candidate evidence.
FAKE_CODEX = """#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if "--version" in argv:
    print("codex-stub 0.0.0")
    raise SystemExit(0)
if "--help" in argv:
    print("exec --json --ephemeral --sandbox --output-last-message")
    raise SystemExit(0)
sys.stdin.read()
print('{"type":"thread.started","thread_id":"stub"}')
print('{"type":"error","message":"stream disconnected before completion"}')
print('{"type":"turn.failed","error":{"message":"stream disconnected"}}')
raise SystemExit(%d)
"""


def make_fake_codex(path: Path, exit_code: int) -> Path:
    path.write_text(FAKE_CODEX % exit_code, encoding="utf-8")
    path.chmod(0o755)
    return path


def run_live(
    suite: Path,
    runs_dir: Path,
    skill_root: Path,
    executable: Path,
    host: str = "codex",
    policy: str = "workspace-write",
) -> Path:
    return run_module.execute(
        argparse.Namespace(
            suite=suite,
            configuration="candidate",
            skill_root=skill_root,
            host=host,
            policy=policy,
            runs_dir=runs_dir,
            model=None,
            executable=str(executable),
            timeout_seconds=30,
            allow_validator=[],
            track="visible",
        )
    )


class ContractTests(unittest.TestCase):
    def test_duplicate_json_key_rejected(self) -> None:
        with self.assertRaises(forge_core.ForgeError):
            forge_core.loads_json_strict('{"a":1,"a":2}')

    def test_nonfinite_json_rejected(self) -> None:
        with self.assertRaises(forge_core.ForgeError):
            forge_core.loads_json_strict('{"a":NaN}')

    def test_unknown_suite_field_rejected(self) -> None:
        value = suite_value()
        value["winner"] = "candidate"
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(value)

    def test_duplicate_case_id_rejected(self) -> None:
        case = execution_case()
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case, dict(case)]))

    def test_routing_prompt_cannot_name_skill(self) -> None:
        case = execution_case(
            plane="routing",
            prompt="Use test-skill for this task.",
            expectations=[{"kind": "selected_skill", "value": "test-skill"}],
        )
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case]))

    def test_routing_case_has_closed_expectation(self) -> None:
        case = execution_case(
            plane="routing",
            expectations=[{"kind": "stdout_equals", "value": "ok\n"}],
        )
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case]))

    def test_critical_contains_only_rejected(self) -> None:
        case = execution_case(expectations=[{"kind": "stdout_contains", "value": "ok"}])
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case]))

    def test_critical_file_exists_only_rejected(self) -> None:
        case = execution_case(expectations=[{"kind": "file_exists", "path": "out.txt"}])
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case]))

    def test_empty_stdout_exact_is_valid(self) -> None:
        case = execution_case(expectations=[{"kind": "stdout_equals", "value": ""}])
        validated = forge_core.validate_suite(suite_value(cases=[case]))
        self.assertEqual(validated["cases"][0]["expectations"][0]["value"], "")

    def test_parent_path_rejected(self) -> None:
        case = execution_case(fixture="../escape")
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_suite(suite_value(cases=[case]))

    def test_snapshot_matches_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_skill(root / "test-skill")
            destination = root / "copy"
            digest = forge_core.snapshot_tree(source, destination)
            self.assertEqual(digest, forge_core.tree_digest(destination))

    def test_snapshot_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_skill(root / "test-skill")
            (source / "target.txt").write_text("x", encoding="utf-8")
            try:
                os.symlink("target.txt", source / "link.txt")
            except OSError as exc:
                self.skipTest(str(exc))
            with self.assertRaises(forge_core.ForgeError):
                forge_core.snapshot_tree(source, root / "copy")


class CheckTests(unittest.TestCase):
    def test_valid_skill_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            report = check_module.inspect_skill(skill)
            self.assertTrue(report["structural_valid"])

    def test_missing_skill_md_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = check_module.inspect_skill(Path(temporary))
            self.assertFalse(report["structural_valid"])

    def test_frontmatter_name_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill", name="wrong-name")
            report = check_module.inspect_skill(skill)
            self.assertFalse(report["structural_valid"])
            self.assertEqual(report["expected_name_source"], "directory")

    def test_isolated_directory_name_is_not_the_identity(self) -> None:
        """A candidate authored in an isolated directory keeps its own name."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root / "candidate", name="line-sorter")
            report = check_module.inspect_skill(skill, expected_name="line-sorter")
            self.assertTrue(report["structural_valid"])
            self.assertEqual(report["expected_name_source"], "explicit")

    def test_suite_supplies_the_expected_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root / "candidate", name="test-skill")
            suite = root / "suite.json"
            write_json(suite, suite_value())
            fixture_response(root, "candidate")
            report = check_module.inspect_skill(skill, suite)
            self.assertTrue(report["structural_valid"])
            self.assertEqual(report["expected_name_source"], "suite")
            self.assertEqual(report["expected_name"], "test-skill")

    def test_explicit_name_overrides_the_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root / "candidate", name="renamed-skill")
            suite = root / "suite.json"
            write_json(suite, suite_value())
            fixture_response(root, "candidate")
            report = check_module.inspect_skill(skill, suite, "renamed-skill")
            self.assertTrue(report["structural_valid"])
            self.assertEqual(report["expected_name_source"], "explicit")

    def test_suite_name_mismatch_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root / "candidate", name="unrelated-skill")
            suite = root / "suite.json"
            write_json(suite, suite_value())
            fixture_response(root, "candidate")
            report = check_module.inspect_skill(skill, suite)
            self.assertFalse(report["structural_valid"])
            self.assertTrue(
                any("unrelated-skill" in item for item in report["errors"])
            )

    def test_shipped_fixtures_pass_their_own_checker(self) -> None:
        """The checker must accept the candidates this project ships."""
        for skill_root, suite in (
            ("create/candidate", "create/suite.json"),
            ("optimize/baseline", "optimize/suite.json"),
            ("optimize/candidate", "optimize/suite.json"),
        ):
            with self.subTest(skill_root=skill_root):
                report = check_module.inspect_skill(
                    SKILL_ROOT / "fixtures" / skill_root,
                    SKILL_ROOT / "fixtures" / suite,
                )
                self.assertEqual(report["errors"], [])
                self.assertTrue(report["structural_valid"])

    def test_todo_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill", body="TODO: finish")
            report = check_module.inspect_skill(skill)
            self.assertFalse(report["structural_valid"])

    def test_cache_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            cache = skill / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "x.pyc").write_bytes(b"cache")
            report = check_module.inspect_skill(skill)
            self.assertFalse(report["structural_valid"])

    def test_sensitive_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            (skill / ".env").write_text("TOKEN=x", encoding="utf-8")
            report = check_module.inspect_skill(skill)
            self.assertFalse(report["structural_valid"])

    def test_ast_risk_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            scripts = skill / "scripts"
            scripts.mkdir()
            (scripts / "danger.py").write_text("eval(input())\n", encoding="utf-8")
            report = check_module.inspect_skill(skill)
            self.assertTrue(report["structural_valid"])
            self.assertEqual(report["risk_findings"][0]["rule"], "dynamic-code")

    def test_unparsed_script_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            scripts = skill / "scripts"
            scripts.mkdir()
            (scripts / "broken.py").write_text("if :\n", encoding="utf-8")
            report = check_module.inspect_skill(skill)
            self.assertTrue(report["risk_unknowns"])

    def test_nested_reference_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            skill = make_skill(Path(temporary) / "test-skill")
            references = skill / "references"
            references.mkdir()
            (references / "a.md").write_text("See [b](b.md).\n", encoding="utf-8")
            (references / "b.md").write_text("# B\n", encoding="utf-8")
            report = check_module.inspect_skill(skill)
            self.assertTrue(any("nested local references" in item for item in report["warnings"]))


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.skill = make_skill(self.root / "test-skill")
        self.suite = self.root / "suite.json"
        write_json(self.suite, suite_value())
        fixture_response(self.root, "candidate")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_candidate_requires_skill_root(self) -> None:
        with self.assertRaises(forge_core.ForgeError):
            run_fixture(self.suite, self.root / "runs", "candidate", None)

    def test_no_skill_rejects_skill_root(self) -> None:
        write_json(self.suite, suite_value(mode="no_skill"))
        fixture_response(self.root, "no_skill")
        with self.assertRaises(forge_core.ForgeError):
            run_fixture(self.suite, self.root / "runs", "no_skill", self.skill)

    def test_fixture_run_records_raw_observation(self) -> None:
        run_dir = run_fixture(self.suite, self.root / "runs", "candidate", self.skill)
        line = (run_dir / "results.jsonl").read_text(encoding="utf-8").strip()
        record = json.loads(line)
        self.assertEqual(record["status"], "completed")
        self.assertNotIn("passed", record)
        self.assertNotIn("winner", record)

    def test_fixture_writes_and_hashes_artifact(self) -> None:
        case = execution_case(
            expectations=[
                {
                    "kind": "json_equals",
                    "path": "output.json",
                    "value": {"ok": True},
                }
            ]
        )
        write_json(self.suite, suite_value(cases=[case]))
        fixture_response(self.root, "candidate", artifacts={"output.json": "{\"ok\":true}\n"})
        run_dir = run_fixture(self.suite, self.root / "runs", "candidate", self.skill)
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertIn("output.json", record["artifacts"])
        self.assertEqual(record["artifact_delta"]["created"], ["output.json"])

    def test_fixture_routing_telemetry_is_recorded(self) -> None:
        case = execution_case(
            plane="routing",
            prompt="Organize this configuration into stable order.",
            expectations=[{"kind": "selected_skill", "value": "test-skill"}],
        )
        write_json(self.suite, suite_value(cases=[case]))
        fixture_response(self.root, "candidate", selected_skill="test-skill")
        run_dir = run_fixture(self.suite, self.root / "runs", "candidate", self.skill)
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["selected_skill"], "test-skill")

    def test_validator_is_not_run_without_allowlist(self) -> None:
        validator = self.root / "checks" / "ok.py"
        validator.parent.mkdir()
        validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
        case = execution_case(
            expectations=[{"kind": "validator", "path": "checks/ok.py"}]
        )
        write_json(self.suite, suite_value(cases=[case]))
        run_dir = run_fixture(self.suite, self.root / "runs", "candidate", self.skill)
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["validators"][0]["status"], "not_run")

    def test_allowed_validator_runs(self) -> None:
        validator = self.root / "checks" / "ok.py"
        validator.parent.mkdir()
        validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
        case = execution_case(
            expectations=[{"kind": "validator", "path": "checks/ok.py"}]
        )
        write_json(self.suite, suite_value(cases=[case]))
        run_dir = run_fixture(
            self.suite,
            self.root / "runs",
            "candidate",
            self.skill,
            ["checks/ok.py"],
        )
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["validators"][0]["status"], "completed")

    def test_validator_artifact_drift_is_integrity_error(self) -> None:
        validator = self.root / "checks" / "mutate.py"
        validator.parent.mkdir()
        validator.write_text("from pathlib import Path\nPath('changed.txt').write_text('x')\n", encoding="utf-8")
        case = execution_case(
            expectations=[{"kind": "validator", "path": "checks/mutate.py"}]
        )
        write_json(self.suite, suite_value(cases=[case]))
        run_dir = run_fixture(
            self.suite,
            self.root / "runs",
            "candidate",
            self.skill,
            ["checks/mutate.py"],
        )
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "integrity_error")


class LiveHostFailureTests(unittest.TestCase):
    """A host transport failure must never become candidate evidence."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.skill = make_skill(self.root / "test-skill")
        self.suite = self.root / "suite.json"
        write_json(
            self.suite,
            suite_value(
                cases=[
                    execution_case(
                        fixture=None,
                        expectations=[
                            {
                                "kind": "json_equals",
                                "path": "out.json",
                                "value": {"ok": True},
                            }
                        ],
                    )
                ]
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_nonzero_exit_is_infra_error(self) -> None:
        executable = make_fake_codex(self.root / "fake-codex", 1)
        run_dir = run_live(self.suite, self.root / "runs", self.skill, executable)
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "infra_error")

    def test_turn_failed_with_zero_exit_is_infra_error(self) -> None:
        executable = make_fake_codex(self.root / "fake-codex", 0)
        run_dir = run_live(self.suite, self.root / "runs", self.skill, executable)
        record = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "infra_error")
        self.assertIn("codex turn failed", record["reason"])

    def test_transport_failure_scores_inconclusive_not_failed(self) -> None:
        executable = make_fake_codex(self.root / "fake-codex", 1)
        run_dir = run_live(self.suite, self.root / "runs", self.skill, executable)
        report = score_module.build_report(self.suite, [run_dir])
        self.assertEqual(report["decision"], "inconclusive")
        self.assertNotIn("candidate_gain_on_selected_cases", report["claims"]["disproven"])


class ObservabilityTests(unittest.TestCase):
    """A live host cannot observe exact stdout, exit status, or routing."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.skill = make_skill(self.root / "test-skill")
        self.suite = self.root / "suite.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _live_report(self, case: dict[str, object]) -> dict[str, object]:
        write_json(self.suite, suite_value(cases=[case]))
        executable = self.root / "stub-codex"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "argv = sys.argv[1:]\n"
            "if '--version' in argv:\n"
            "    print('codex-stub 0.0.0')\n"
            "    raise SystemExit(0)\n"
            "if '--help' in argv:\n"
            "    print('exec --json --ephemeral --sandbox --output-last-message')\n"
            "    raise SystemExit(0)\n"
            "sys.stdin.read()\n"
            "print('I will now consider the task.')\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        run_dir = run_live(self.suite, self.root / "runs", self.skill, executable)
        return score_module.build_report(self.suite, [run_dir])

    def test_stdout_equals_is_not_run_on_a_live_host(self) -> None:
        report = self._live_report(execution_case(fixture=None))
        self.assertEqual(report["cases"][0]["configurations"]["candidate"], "not_run")
        self.assertTrue(report["unobservable_checks"])
        self.assertEqual(report["unobservable_checks"][0]["kind"], "stdout_equals")

    def test_stdout_equals_is_objective_on_the_fixture_host(self) -> None:
        write_json(self.suite, suite_value())
        fixture_response(self.root, "candidate")
        run_dir = run_fixture(self.suite, self.root / "runs", "candidate", self.skill)
        report = score_module.build_report(self.suite, [run_dir])
        self.assertEqual(report["cases"][0]["configurations"]["candidate"], "passed")
        self.assertEqual(report["unobservable_checks"], [])

    def test_indicative_check_does_not_decide_the_case(self) -> None:
        case = execution_case(
            fixture=None,
            critical=False,
            expectations=[
                {"kind": "stdout_contains", "value": "definitely-absent-token"}
            ],
        )
        report = self._live_report(case)
        # The text match fails, but it must not fail the case.
        self.assertEqual(report["cases"][0]["configurations"]["candidate"], "not_run")
        self.assertTrue(report["indicative_observations"])
        self.assertEqual(report["indicative_observations"][0]["status"], "failed")


def sealed_case(**updates: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "sealed-core",
        "source": "synthetic",
        "plane": "execution",
        "category": "boundary",
        "critical": True,
        "prompt": "Perform the withheld test task.",
        "fixture": "cases/sealed-core",
        "expectations": [{"kind": "stdout_equals", "value": "sealed-ok\n"}],
    }
    case.update(updates)
    return case


class SealedPairingTests(unittest.TestCase):
    """A sealed suite must be a disjoint extension of the visible one."""

    def test_shared_case_id_rejected(self) -> None:
        visible = forge_core.validate_suite(suite_value())
        sealed = forge_core.validate_suite(suite_value(cases=[execution_case()]))
        with self.assertRaises(forge_core.ForgeError) as caught:
            forge_core.validate_sealed_pairing(visible, sealed)
        self.assertIn("share case ids", str(caught.exception))

    def test_different_skill_rejected(self) -> None:
        visible = forge_core.validate_suite(suite_value())
        other = suite_value(cases=[sealed_case()])
        other["skill"] = "other-skill"
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_sealed_pairing(
                visible, forge_core.validate_suite(other)
            )

    def test_different_mode_rejected(self) -> None:
        visible = forge_core.validate_suite(suite_value())
        sealed = forge_core.validate_suite(
            suite_value(mode="optimize", cases=[sealed_case()])
        )
        with self.assertRaises(forge_core.ForgeError):
            forge_core.validate_sealed_pairing(visible, sealed)

    def test_disjoint_pairing_accepted(self) -> None:
        visible = forge_core.validate_suite(suite_value())
        sealed = forge_core.validate_suite(suite_value(cases=[sealed_case()]))
        forge_core.validate_sealed_pairing(visible, sealed)


class SealedTrackTests(unittest.TestCase):
    """Held-out results cap claims; they never replace the visible decision."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.skill = make_skill(self.root / "test-skill")
        self.visible = self.root / "suite.json"
        self.sealed = self.root / "suite.sealed.json"
        write_json(self.visible, suite_value())
        write_json(self.sealed, suite_value(cases=[sealed_case()]))
        fixture_response(self.root, "candidate")
        self._sealed_response("sealed-ok\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _sealed_response(self, stdout: str) -> None:
        write_json(
            self.root / "cases" / "sealed-core" / "response.candidate.json",
            {
                "stdout": stdout,
                "stderr": "",
                "exit_code": 0,
                "selected_skill": None,
                "artifacts": {},
            },
        )

    def _report(self) -> dict[str, object]:
        visible_run = run_fixture(
            self.visible, self.root / "runs", "candidate", self.skill
        )
        sealed_run = run_fixture(
            self.sealed, self.root / "runs", "candidate", self.skill, track="sealed"
        )
        return score_module.build_report(
            self.visible, [visible_run], self.sealed, [sealed_run]
        )

    def test_run_records_its_track(self) -> None:
        run_dir = run_fixture(
            self.sealed, self.root / "runs", "candidate", self.skill, track="sealed"
        )
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["track"], "sealed")

    def test_sealed_pass_confirms_and_proves_holdout(self) -> None:
        self._sealed_response("sealed-ok\n")
        report = self._report()
        self.assertEqual(report["decision"], "handoff_candidate")
        self.assertEqual(report["sealed"]["verdict"], "confirms")
        self.assertIn(
            "visible_decision_reproduced_on_held_out_cases",
            report["claims"]["proven"],
        )

    def test_sealed_failure_does_not_overturn_the_decision(self) -> None:
        self._sealed_response("wrong-output\n")
        report = self._report()
        # The visible decision stands; the holdout claim is disproven instead.
        self.assertEqual(report["decision"], "handoff_candidate")
        self.assertEqual(report["sealed"]["decision"], "reject_candidate")
        self.assertEqual(report["sealed"]["verdict"], "contradicts")
        self.assertIn(
            "visible_decision_reproduced_on_held_out_cases",
            report["claims"]["disproven"],
        )

    def test_absent_sealed_suite_leaves_holdout_unverified(self) -> None:
        visible_run = run_fixture(
            self.visible, self.root / "runs", "candidate", self.skill
        )
        report = score_module.build_report(self.visible, [visible_run])
        self.assertIsNone(report["sealed"])
        self.assertIn("behavior_on_held_out_cases", report["claims"]["unverified"])
        self.assertIn("no_held_out_cases", report["limitations"])

    def test_track_mismatch_rejected(self) -> None:
        visible_run = run_fixture(
            self.visible, self.root / "runs", "candidate", self.skill
        )
        self._sealed_response("sealed-ok\n")
        # A visible-track run cannot be supplied as sealed evidence.
        mislabelled = run_fixture(
            self.sealed, self.root / "runs", "candidate", self.skill, track="visible"
        )
        with self.assertRaises(forge_core.ForgeError) as caught:
            score_module.build_report(
                self.visible, [visible_run], self.sealed, [mislabelled]
            )
        self.assertIn("track", str(caught.exception))

    def test_sealed_runs_require_a_sealed_suite(self) -> None:
        visible_run = run_fixture(
            self.visible, self.root / "runs", "candidate", self.skill
        )
        with self.assertRaises(forge_core.ForgeError):
            score_module.build_report(self.visible, [visible_run], None, [visible_run])


class ScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _copy_fixture(self, name: str) -> Path:
        source = SKILL_ROOT / "fixtures" / name
        destination = self.root / name
        shutil.copytree(source, destination)
        return destination

    def test_create_golden_handoff(self) -> None:
        fixture = self._copy_fixture("create")
        run_dir = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        report = score_module.build_report(fixture / "suite.json", [run_dir])
        self.assertEqual(report["decision"], "handoff_candidate")

    def test_optimize_golden_adopts(self) -> None:
        fixture = self._copy_fixture("optimize")
        baseline = run_fixture(
            fixture / "suite.json", self.root / "runs", "baseline", fixture / "baseline"
        )
        candidate = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        report = score_module.build_report(fixture / "suite.json", [baseline, candidate])
        self.assertEqual(report["decision"], "adopt_candidate_for_selected_cases")
        self.assertTrue(report["comparison"]["improvements"])

    def test_no_skill_golden_supported(self) -> None:
        fixture = self._copy_fixture("no-skill")
        run_dir = run_fixture(
            fixture / "suite.json", self.root / "runs", "no_skill", None
        )
        report = score_module.build_report(fixture / "suite.json", [run_dir])
        self.assertEqual(report["decision"], "no_skill_supported_for_selected_cases")

    def test_optimize_without_gain_keeps_baseline(self) -> None:
        fixture = self._copy_fixture("optimize")
        for case in ("core-canonical-bytes", "duplicate-key-boundary"):
            base = fixture / "cases" / case / "response.baseline.json"
            candidate = fixture / "cases" / case / "response.candidate.json"
            candidate.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
        baseline = run_fixture(
            fixture / "suite.json", self.root / "runs", "baseline", fixture / "baseline"
        )
        candidate = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        report = score_module.build_report(fixture / "suite.json", [baseline, candidate])
        self.assertEqual(report["decision"], "keep_baseline")

    def test_critical_candidate_failure_keeps_baseline(self) -> None:
        fixture = self._copy_fixture("optimize")
        core = fixture / "cases" / "core-canonical-bytes"
        correct = (core / "response.candidate.json").read_text(encoding="utf-8")
        wrong = (core / "response.baseline.json").read_text(encoding="utf-8")
        (core / "response.baseline.json").write_text(correct, encoding="utf-8")
        (core / "response.candidate.json").write_text(wrong, encoding="utf-8")
        boundary = fixture / "cases" / "duplicate-key-boundary"
        (boundary / "response.baseline.json").write_text(
            (boundary / "response.candidate.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        baseline = run_fixture(
            fixture / "suite.json", self.root / "runs", "baseline", fixture / "baseline"
        )
        candidate = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        report = score_module.build_report(fixture / "suite.json", [baseline, candidate])
        self.assertEqual(report["decision"], "keep_baseline")

    def test_incomplete_matrix_is_inconclusive(self) -> None:
        fixture = self._copy_fixture("create")
        run_dir = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        results = run_dir / "results.jsonl"
        lines = results.read_text(encoding="utf-8").splitlines()
        results.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["result_count"] -= 1
        manifest["results_digest"] = forge_core.digest_file(results)
        write_json(manifest_path, manifest)
        report = score_module.build_report(fixture / "suite.json", [run_dir])
        self.assertEqual(report["decision"], "inconclusive")

    def test_results_digest_mismatch_rejected(self) -> None:
        fixture = self._copy_fixture("create")
        run_dir = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        with (run_dir / "results.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{}\n")
        with self.assertRaises(forge_core.ForgeError):
            score_module.build_report(fixture / "suite.json", [run_dir])

    def test_suite_digest_mismatch_rejected(self) -> None:
        create = self._copy_fixture("create")
        no_skill = self._copy_fixture("no-skill")
        run_dir = run_fixture(
            create / "suite.json", self.root / "runs", "candidate", create / "candidate"
        )
        with self.assertRaises(forge_core.ForgeError):
            score_module.build_report(no_skill / "suite.json", [run_dir])

    def test_fixture_claim_cap_is_reported(self) -> None:
        fixture = self._copy_fixture("create")
        run_dir = run_fixture(
            fixture / "suite.json", self.root / "runs", "candidate", fixture / "candidate"
        )
        report = score_module.build_report(fixture / "suite.json", [run_dir])
        self.assertIn("fixture_host_only", report["limitations"])
        self.assertIn("automatic_routing", report["claims"]["not_run"])


class PackagingTests(unittest.TestCase):
    """A distribution ships runtime bytes only and rechecks against current bytes."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        for current, directories, files in os.walk(self.root):
            for name in directories + files:
                path = Path(current) / name
                try:
                    path.chmod(0o700)
                except OSError:
                    pass
        self.temporary.cleanup()

    def _build(self, host: str = "claude") -> tuple[Path, Path]:
        output = self.root / host / "skill-forge"
        manifest = self.root / host / "skill-forge.manifest.json"
        package_module.build(SKILL_ROOT, host, output, manifest)
        return output, manifest

    def test_payload_excludes_development_material(self) -> None:
        output, _ = self._build()
        shipped = {
            entry.path
            for entry in forge_core.inspect_tree(output, reject_unsafe=True)
            if entry.kind == "file"
        }
        self.assertIn("SKILL.md", shipped)
        self.assertIn("scripts/run.py", shipped)
        self.assertIn("references/evidence.md", shipped)
        self.assertFalse(any(path.startswith("fixtures/") for path in shipped))
        self.assertFalse(any(path.startswith("tests/") for path in shipped))
        self.assertNotIn("scripts/package.py", shipped)

    def test_codex_ships_the_interface_declaration(self) -> None:
        codex, _ = self._build("codex")
        claude, _ = self._build("claude")

        def shipped(root: Path) -> set[str]:
            return {
                entry.path
                for entry in forge_core.inspect_tree(root, reject_unsafe=True)
                if entry.kind == "file"
            }

        self.assertIn("agents/openai.yaml", shipped(codex))
        self.assertNotIn("agents/openai.yaml", shipped(claude))

    def test_verify_accepts_a_fresh_build(self) -> None:
        output, manifest = self._build()
        receipt = package_module.verify(output, manifest)
        self.assertTrue(receipt["tree_read_only"])
        self.assertEqual(receipt["claim_cap"], "byte_binding_only")

    def test_verify_rejects_a_modified_file(self) -> None:
        output, manifest = self._build()
        target = output / "SKILL.md"
        target.chmod(0o600)
        target.write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(forge_core.ForgeError) as caught:
            package_module.verify(output, manifest)
        self.assertIn("digest changed", str(caught.exception))

    def test_verify_rejects_an_unexpected_file(self) -> None:
        output, manifest = self._build()
        output.chmod(0o700)
        (output / "extra.md").write_text("x\n", encoding="utf-8")
        with self.assertRaises(forge_core.ForgeError) as caught:
            package_module.verify(output, manifest)
        self.assertIn("unexpected files", str(caught.exception))

    def test_verify_rejects_a_missing_file(self) -> None:
        output, manifest = self._build()
        references = output / "references"
        references.chmod(0o700)
        (references / "risk.md").chmod(0o600)
        (references / "risk.md").unlink()
        with self.assertRaises(forge_core.ForgeError) as caught:
            package_module.verify(output, manifest)
        self.assertIn("missing files", str(caught.exception))

    def test_build_refuses_an_existing_destination(self) -> None:
        output, manifest = self._build()
        with self.assertRaises(forge_core.ForgeError):
            package_module.build(SKILL_ROOT, "claude", output, manifest)

    def test_packaged_scripts_run_standalone(self) -> None:
        """The shipped tree must run check/run/score without the source tree."""
        output, _ = self._build()
        workspace = self.root / "work"
        shutil.copytree(SKILL_ROOT / "fixtures" / "create", workspace)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        checked = subprocess.run(
            [
                sys.executable,
                str(output / "scripts" / "check.py"),
                str(workspace / "candidate"),
                "--suite",
                str(workspace / "suite.json"),
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        run = subprocess.run(
            [
                sys.executable,
                str(output / "scripts" / "run.py"),
                "--suite",
                str(workspace / "suite.json"),
                "--configuration",
                "candidate",
                "--skill-root",
                str(workspace / "candidate"),
                "--host",
                "fixture",
                "--runs-dir",
                str(self.root / "runs"),
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        scored = subprocess.run(
            [
                sys.executable,
                str(output / "scripts" / "score.py"),
                "--suite",
                str(workspace / "suite.json"),
                "--run",
                run.stdout.strip(),
                "--output-dir",
                str(self.root / "report"),
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(scored.returncode, 0, scored.stderr)
        report = json.loads(
            (self.root / "report" / "report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["decision"], "handoff_candidate")


if __name__ == "__main__":
    unittest.main()
