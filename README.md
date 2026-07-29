# Skill Forge

Build the smallest Skill that real task evidence justifies, and say exactly what the evidence does and does not prove.

Skill Forge is a Skill for writing Skills. It gives an agent a workflow (freeze the acceptance cases before the candidate exists), three small scripts (structure check, case runner, scorer), and a report format that separates what was proven from what was merely observed. It stops at a candidate handoff: it does not install, publish, or commit.

Python 3.10+, standard library only. No network calls, no dependencies.

## Why it looks like this

Most Skill-authoring tooling fails in one of two ways. It either generates prose with no way to tell whether the result is better than nothing, or it grows a compliance layer so large that nobody completes a run.

Skill Forge takes a narrow position:

- **The acceptance cases are frozen in a different context than the candidate.** One agent writing both leaks its implementation plan into the tests, and ordering alone cannot prevent that because the leak precedes the freeze.
- **An unobservable check is not a failure.** A live host returns assistant prose, so exact-stdout expectations score `not_run`, never `failed`. Transport failures score `infra_error`. A network interruption must never produce a verdict about a candidate.
- **Stopping is a first-class result.** `keep_baseline` and `no_skill_supported_for_selected_cases` require evidence, exactly like adoption does.
- **Claims are capped in the report, not in prose.** Every report states its scope: selected cases only, not installed, not released, plus `fixture_host_only` and `no_held_out_cases` when they apply.

## Install

```bash
cp -r skill-forge ~/.claude/skills/skill-forge     # Claude Code
cp -r skill-forge ~/.codex/skills/skill-forge      # Codex
```

Invoke explicitly with `/skill-forge` or `$skill-forge`. It does not self-select from ordinary conversation.

To ship a verified tree instead of copying source, see [Packaging](#packaging).

## The workflow

```text
NEED -> FRAME -> FREEZE -> DRAFT/STAGE
     -> CHECK -> RUN -> SCORE -> DECIDE
     -> CAUSAL ITERATION -> HANDOFF
```

Pick one mode: `reuse`, `create`, `optimize`, or `no_skill`. Then freeze a goal card without copying the user's proposed implementation, freeze the suite, and only then build.

Full instructions are in [SKILL.md](SKILL.md). The references are loaded conditionally:

| Reference | Read it before |
|---|---|
| [authoring.md](references/authoring.md) | drafting or revising a candidate |
| [evaluation.md](references/evaluation.md) | designing cases or reading a decision |
| [evidence.md](references/evidence.md) | writing a suite or interpreting a report |
| [hosts.md](references/hosts.md) | running live Codex or Claude cases |
| [risk.md](references/risk.md) | the Skill has scripts, writes files, or touches credentials |

## Three-stage isolation

The suite is written by an agent that never sees the candidate, and the candidate is written by an agent that never sees the sealed cases:

```text
main conversation   clarify the goal -> requirements.md      (no implementation talk)
        |  passes requirements.md only
case subagent       suite.json + suite.sealed.json           (must ask about near-negatives)
        |  passes requirements.md + suite.json only
build subagent      the candidate
```

The holdout is enforced by not passing the file. There is no access-control contract to bypass, and no relabelling can turn a visible case into a sealed one afterwards.

Two agents from the same model share blind spots, so isolation prevents contamination but not ignorance. The user reviewing the proposed cases in plain language is the only heterogeneous check, and it is not optional. The case agent must also ask, once: *which requests could this Skill wrongly steal?* That answer is the user's alone.

## The suite

One strict JSON file per track, frozen before the candidate is observed. Baseline and candidate paths are passed to the runner, never stored in the suite.

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
        {"kind": "json_equals", "path": "output.json", "value": {"a": 1}}
      ]
    }
  ]
}
```

Every case carries a `source` label (`observed`, `user_confirmed`, `synthetic`, `assumed`) and a `category` (`core`, `boundary`, `failure`, `near_negative`). Critical execution cases require a strong deterministic expectation; `contains` alone will be rejected.

`plane: routing` cases measure whether a host selects the Skill without naming it, so the loader rejects a routing prompt that mentions the Skill name.

## Commands

```bash
# structure, hygiene, and AST risk hints
python3 scripts/check.py <candidate> --suite <suite.json>

# raw observations into a fresh run directory
python3 scripts/run.py --suite <suite.json> --configuration candidate \
  --skill-root <candidate> --host fixture --runs-dir <runs>

# reduce runs into a report; sealed evidence is a separate track
python3 scripts/score.py --suite <suite.json> --run <run> \
  [--sealed-suite <suite.sealed.json> --sealed-run <sealed-run>] \
  --output-dir <report>
```

`run.py` records observations only. `score.py` derives every verdict from those raw bytes; a caller cannot write `passed` into a result.

## What a host can actually observe

An expectation only decides a case when the chosen host can observe it. The suite stays host-neutral; the scorer resolves this from the run manifest.

| Expectation | `fixture` | live host |
|---|---|---|
| `json_equals`, `file_sha256`, `validator`, `file_exists` | objective | objective, needs `--policy workspace-write` |
| `stdout_contains`, `stdout_not_contains` | objective | indicative, never decides a case |
| `stdout_equals`, `exit_code`, `selected_skill` | objective | unobservable, scores `not_run` |

This matters more than it looks. As of the current version, **no live host/policy combination produces positive execution evidence**: `read-only` leaves the model unable to read case inputs, `claude` + `workspace-write` is unimplemented, and routing telemetry does not exist on either host. Artifact expectations under `codex` + `workspace-write` are the intended path.

The fixture host replays frozen responses. It proves the pipeline works and nothing about a live model, which is why its reports carry `fixture_host_only`.

## Reading a report

Real output from `fixtures/optimize`, with the candidate regressed on the core case:

```text
Decision: `keep_baseline`

| Case                   | Plane     | Critical | Results                           |
| core-canonical-bytes   | execution | yes      | baseline=passed, candidate=failed |
| duplicate-key-boundary | execution | yes      | baseline=failed, candidate=passed |

## Claims

- Proven: fixture_pipeline_completed_on_selected_cases
- Disproven: candidate_gain_on_selected_cases
- Unverified: generalization_beyond_selected_cases, long_term_stability, runtime_safety,
  host_activation, behavior_on_held_out_cases
- Not run: automatic_routing
```

One case improved, but a critical case regressed, so the gain claim is disproven rather than averaged away. `fixture_host_only` is what `Proven` reduces to on the fixture host, and `behavior_on_held_out_cases` is unverified because no sealed suite was supplied.

A live run adds a section naming what could not be judged:

```text
## Not observable on this host
- `core-sort` / `stdout_equals`: a live host returns assistant prose, not exact task stdout
```

Decisions are `handoff_candidate`, `reject_candidate`, `adopt_candidate_for_selected_cases`, `keep_baseline`, `no_skill_supported_for_selected_cases`, `no_skill_not_supported_for_selected_cases`, or `inconclusive`.

Sealed results cap claims rather than replacing the decision: agreement proves `visible_decision_reproduced_on_held_out_cases`, contradiction disproves it, and the visible decision still stands. A contradiction is a real limit on generalization, not grounds for quietly reclassifying the candidate.

## Packaging

```bash
python3 scripts/package.py build --source . --host codex \
  --output <dist>/skill-forge --manifest <dist>/skill-forge.manifest.json
python3 scripts/package.py verify --candidate <dist>/skill-forge \
  --manifest <dist>/skill-forge.manifest.json --output <dist>/skill-forge-receipt.json
```

Ships `SKILL.md`, `VERSION`, `LICENSE`, `references/`, `scripts/`, plus `agents/` for Codex. Fixtures, tests, and the packaging script are authoring inputs and never ship.

The receipt is a byte binding with `claim_cap: byte_binding_only`. It proves the tree matches its manifest and that POSIX write bits were removed. It is not a signature, does not install anything, and does not prove a host loaded the Skill.

## Layout

```text
skill-forge/
├── SKILL.md              the workflow an agent follows
├── references/           conditional detail, one level deep
├── scripts/              check, run, score, package
├── fixtures/             create / optimize / no-skill pipeline fixtures
└── tests/                67 regressions over all four scripts
```

```bash
python3 -m unittest discover -s tests -v
```

## Limits

Stated plainly, because the reports state them too:

- No live host currently yields positive execution evidence (see the observability table).
- Routing and near-negative trigger behavior cannot be tested; those cases stay `not_run`.
- Digests identify compared bytes and prevent accidental result mixing. They are not signatures and offer nothing against someone with write access to the same filesystem.
- `check.py` AST findings are planning hints. No findings is not a safety certificate, and non-Python scripts are recorded as unscanned rather than cleared.
- A passing suite covers the selected cases only. Generalization, long-term stability, host activation, and runtime safety stay unverified by design.
- Delivery stops at a handoff. Installation is `cp -r` plus a `.bak`, done by the user.

## License

MIT-NonCommercial. See [LICENSE](LICENSE).
