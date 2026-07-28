# Evidence Contracts

Use this reference when writing a suite, inspecting run artifacts, or interpreting a report.

## Suite contract

Use strict UTF-8 JSON. Duplicate keys, unknown fields, non-finite numbers, parent traversal, and absolute paths are invalid.

```json
{
  "version": 1,
  "skill": "canonical-json",
  "mode": "optimize",
  "reps": 1,
  "cases": [
    {
      "id": "core-canonicalize",
      "source": "observed",
      "plane": "execution",
      "category": "core",
      "critical": true,
      "prompt": "Canonicalize payload.json into output.json.",
      "fixture": "cases/core-canonicalize",
      "expectations": [
        {
          "kind": "json_equals",
          "path": "output.json",
          "value": {"a": 1, "b": 2}
        }
      ]
    }
  ]
}
```

Supported expectations:

- `selected_skill`: routing telemetry only; value is a Skill name or null.
- `exit_code`: exact process exit.
- `stdout_equals`, `stdout_contains`, `stdout_not_contains`.
- `file_exists`, `file_not_exists`, `file_sha256`.
- `json_equals`: strict parsed JSON equality.
- `validator`: trusted relative path, string argv, and timeout.

Keep validator paths relative to the suite directory. The runner executes only paths explicitly supplied through `--allow-validator`. Validator exit zero is a deterministic observation, not proof that the validator itself is correct or safe.

## Sealed suites

A sealed suite is a second suite withheld from the agent that builds the candidate. It uses the same contract and must be a disjoint extension of the visible suite: same `skill`, same `mode`, same `reps`, no shared case ids, at least one case.

The holdout is enforced by not passing the file to the build agent. There is no access-control contract to bypass, and no relabelling can turn a visible case into a sealed one after the fact.

Run it with `--track sealed`; the run manifest records the track and the scorer rejects a run whose track does not match the suite it was supplied as. Score it with `--sealed-suite` and `--sealed-run`.

Sealed evidence caps claims and never replaces the visible decision:

| Sealed outcome | Effect |
|---|---|
| agrees with the visible decision | proves `visible_decision_reproduced_on_held_out_cases` |
| contradicts it | disproves that claim; the visible decision still stands |
| inconclusive | `held_out_case_confirmation` is not run |
| no sealed suite supplied | `behavior_on_held_out_cases` stays unverified, plus a `no_held_out_cases` limitation |

A contradiction is a limit on generalization, not grounds for silently reclassifying the candidate.

## Observability by host

An expectation only decides a case when the host can actually observe it. The suite stays host-neutral; the scorer resolves observability from the run manifest.

| Expectation | `fixture` | `codex` / `claude` |
|---|---|---|
| `file_exists`, `file_not_exists`, `file_sha256`, `json_equals`, `validator` | objective | objective, and require `--policy workspace-write` |
| `stdout_contains`, `stdout_not_contains` | objective | indicative |
| `stdout_equals` | objective | unobservable |
| `exit_code` | objective | unobservable |
| `selected_skill` | objective | unobservable |

- **objective**: decides pass/fail.
- **indicative**: recorded under `indicative_observations`; never decides a case. A live host returns assistant prose, so a text match is weak evidence about wording, not about task outcome.
- **unobservable**: recorded under `unobservable_checks` and scored `not_run`. A live host returns prose rather than exact task stdout, its exit status describes the CLI rather than the task, and no host exposes route telemetry.

A case whose every expectation is unobservable on the chosen host is `not_run`, never `failed`. Prefer artifact expectations for live runs: the bytes a candidate leaves in the workspace are the only objective channel a real host provides.

## Run contract

Each run has an independent directory:

```text
runs/<run-id>/
├── manifest.json
├── suite.snapshot.json
├── inputs/
├── raw/
├── workspaces/
└── results.jsonl
```

The manifest binds the suite digest, configuration, Skill tree digest, host probe, execution policy, model, repetitions, validator allowlist, result count, and results digest.

Every results line is a raw case observation. It records case, repetition, configuration, status, reason, prompt digest, exact argv, exit code, selected-Skill telemetry, raw stdout/stderr paths and digests, workspace, artifact observations, artifact delta, and validator observations.

Allowed observation statuses:

- `completed`: the host returned successfully; this is not a passing score.
- `not_run`: a required capability or authorization was absent.
- `infra_error`: timeout, process failure, or unavailable infrastructure.
- `integrity_error`: a bound input or validator changed protected bytes.

A host transport failure is `infra_error`, never candidate evidence. The runner detects a non-zero host exit, a Codex `turn.failed` event, and a Claude `is_error` result. A network interruption must never produce `keep_baseline` or `reject_candidate`.

Do not write `passed`, `winner`, or terminal decisions into raw results.

## Identity digests

Use ordinary SHA-256 for suite, Skill trees, validators, raw outputs, and artifact files. These digests prevent accidental result mixing and identify compared bytes. They are not signatures, anti-tamper chains, or evidence against a filesystem owner.

## Report contract

Case status is one of:

- `passed`;
- `failed`;
- `inconclusive`;
- `not_run`.

Keep claims in four separate sections:

- `proven`: directly supported within the selected cases and execution surface;
- `disproven`: contradicted by observed selected cases;
- `unverified`: not established by this evidence design;
- `not_run`: planned evidence that was unavailable or intentionally skipped.

Keep weak text matches and optional model-judge output under `indicative_observations`, and host-unobservable expectations under `unobservable_checks`. Neither can prove gain, trigger installation, or override a critical deterministic regression.

The report always states selected-case scope, installation and release absence, host limitations, and whether only fixture-host observations were used.
