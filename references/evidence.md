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

- `completed`: the host returned; this is not a passing score.
- `not_run`: a required capability or authorization was absent.
- `infra_error`: timeout, process failure, or unavailable infrastructure.
- `integrity_error`: a bound input or validator changed protected bytes.

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

Keep optional model-judge output under `indicative_observations`. It cannot independently prove gain, trigger installation, or override a critical deterministic regression.

The report always states selected-case scope, installation and release absence, host limitations, and whether only fixture-host observations were used.
