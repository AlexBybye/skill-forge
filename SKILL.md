---
name: skill-forge
description: Optimize existing Codex and Claude Skills against immutable baselines, test whether no Skill is sufficient, or create a small Skill only when real task evidence justifies it. Use when explicitly invoked with $skill-forge for candidate-neutral suites, raw observations, bounded evidence claims, and keep-baseline or no-skill outcomes.
---

# Skill Forge

Build the smallest Skill justified by real task evidence. Treat authoring, evaluation, risk, and delivery as separate concerns. Stop at a candidate handoff; do not install, overwrite, commit, publish, or write outside the requested workspace without separate authorization.

## Core workflow

Use this sequence:

```text
NEED -> FRAME -> FREEZE -> DRAFT/STAGE
     -> CHECK -> RUN -> SCORE -> DECIDE
     -> CAUSAL ITERATION -> HANDOFF
```

### 1. Decide need and mode

Choose exactly one executable mode:

- `create`: create a candidate without claiming gain unless a real no-Skill comparison exists.
- `optimize`: preserve an immutable baseline and improve one observed failure.
- `no_skill`: create no candidate and run a candidate-free baseline probe.

If an adjacent Skill is already adequate, record `reuse` as an upstream triage outcome and do not enter an executable suite mode or create a duplicate.

Read [references/authoring.md](references/authoring.md) before drafting or modifying a Skill.

### 2. Freeze the frame

Write a compact goal card before comparing implementation proposals:

- target user and real task;
- observed failure or repeated work;
- desired behavior and explicit non-goals;
- relevant quality dimensions;
- evidence source and acceptance cases;
- cost limit and stop condition.

Derive the goal card without copying the user's proposed implementation. Compare proposals afterward on goal fit, evidence, benefit, cost, risk, reversibility, and validation.

### 3. Freeze the suite in a separate context

Do not write the suite yourself in the conversation that will also design the candidate. A single context leaks its implementation plan into the cases it writes, and freezing order cannot prevent that because the leak happens before the freeze.

Use three stages with real context boundaries:

1. **Requirements.** In this conversation, clarify the goal, real cases, observed failures, and constraints. Write `requirements.md`. Do not discuss implementation. If the user asks how it will work, say that comes after the acceptance standard is fixed.
2. **Cases.** Spawn a subagent whose only input is `requirements.md`. It writes `suite.json` and, when a holdout is wanted, `suite.sealed.json`. It must propose near-negative cases itself and ask the user once: which requests could this Skill wrongly steal? That answer is the user's unique knowledge and cannot be inferred.
3. **Build.** Spawn a second subagent whose only inputs are `requirements.md` and `suite.json`. Never pass `suite.sealed.json` to it. The holdout is enforced by not passing the file; there is no access-control mechanism to bypass.

Show the user the proposed cases in plain language before freezing. Spell out each case so they can actually reject one; do not summarize it as a count.

Keep baseline and candidate paths out of both suites; pass them to the runner. Include real core cases plus relevant boundary, failure, and near-negative cases. Mark every case with `observed`, `user_confirmed`, `synthetic`, or `assumed`. Treat development cases as development cases; do not relabel them as holdout.

Two agents from the same model share blind spots. Isolation prevents contamination, not ignorance, so the user review in stage 2 is the only heterogeneous check and cannot be skipped.

Read [references/evaluation.md](references/evaluation.md) for suite and decision rules. Read [references/evidence.md](references/evidence.md) before authoring a suite or interpreting a report.

### 4. Draft or stage the candidate

Keep the baseline unchanged. Create the candidate in an isolated directory. Admit an instruction only when at least one condition holds:

- a capable model cannot reliably infer it;
- it addresses an observed failure or high-risk case;
- it is host-, project-, or domain-specific;
- it removes repeated work;
- deleting it would fail a real test.

Put persistent behavior in `SKILL.md`, conditional detail in directly linked `references/`, fragile deterministic transformations in `scripts/`, and only genuinely consumed output resources in `assets/`. Delete everything else.

### 5. Check structure and risk hints

Run:

```bash
python3 scripts/check.py <candidate-skill> --suite <suite.json>
```

`frontmatter.name` must equal the name the Skill will install as, not the isolated authoring directory. The checker takes that name from `--expect-name`, then from the frozen suite's `skill`, then from the directory. Pass the suite or `--expect-name` whenever the candidate directory is named something like `candidate/`.

Treat a successful check as structure and input-contract evidence only. AST findings and unknowns inform the risk plan; absence of findings never proves runtime safety.

Read [references/risk.md](references/risk.md) when the Skill contains scripts, writes files, accesses credentials, calls external services, creates shared triggers, or performs high-impact evaluation.

### 6. Run raw observations

Run each configuration into a new directory:

```bash
python3 scripts/run.py \
  --suite <suite.json> \
  --configuration candidate \
  --skill-root <candidate-skill> \
  --host codex \
  --policy read-only \
  --runs-dir <runs-dir>
```

For optimization, run the same frozen suite separately against `baseline` and `candidate`. For `no_skill`, omit `--skill-root` and use `--configuration no_skill`.

Run a sealed suite as its own track, after the candidate is final:

```bash
python3 scripts/run.py \
  --suite <suite.sealed.json> \
  --configuration candidate \
  --skill-root <candidate-skill> \
  --host fixture \
  --track sealed \
  --runs-dir <runs-dir>
```

Use `fixture` only to test the pipeline itself; it cannot prove live model or host behavior. Do not run a custom validator unless the user trusts it and its exact relative path is passed with `--allow-validator`.

Pick the host and policy from what each combination can actually observe:

| Host | Policy | Objective evidence |
|---|---|---|
| `fixture` | `read-only` | every expectation, pipeline only |
| `codex` | `workspace-write` | artifact expectations |
| `codex` | `read-only` | none; the model cannot read case inputs or write artifacts |
| `claude` | `read-only` | none; same limitation |
| `claude` | `workspace-write` | unavailable in v1, rejected by the runner |

A live host observes only the bytes a candidate leaves in the workspace. Exact stdout, process exit status, and route telemetry are unobservable there and score `not_run`, never `failed`. Write artifact expectations for live runs, and expect a prose-only Skill to stay unverified rather than fail. Read [references/evidence.md](references/evidence.md) for the full observability table.

Read [references/hosts.md](references/hosts.md) before live host execution. Do not spend paid-model budget or enable writes beyond the agreed scope implicitly.

### 7. Score without executing

Reduce completed run directories:

```bash
python3 scripts/score.py \
  --suite <suite.json> \
  --run <baseline-run> \
  --run <candidate-run> \
  [--sealed-suite <suite.sealed.json> --sealed-run <sealed-run>] \
  --output-dir <new-report-dir>
```

`score.py` must derive results from raw observations. Never add caller-authored `passed`, `winner`, or terminal facts to `results.jsonl`.

Sealed results are scored as a separate track. They cap or extend the claims and never replace the visible decision: a sealed pass proves `visible_decision_reproduced_on_held_out_cases`, a sealed contradiction disproves it, and no sealed suite leaves `behavior_on_held_out_cases` unverified. Report a contradiction as a real limit on generalization rather than reclassifying the candidate.

### 8. Decide and iterate

Use only evidence-derived outcomes:

- create without a comparison: `handoff_candidate` or `reject_candidate`;
- optimize with an improvement and no critical regression: `adopt_candidate_for_selected_cases`;
- optimize without gain or with critical regression: `keep_baseline`;
- candidate-free baseline passing all critical cases: `no_skill_supported_for_selected_cases`;
- missing, conflicting, timed-out, or unsupported evidence: `inconclusive`.

Apply one causal change at a time. Re-run the original failure and affected regressions. Preserve the best passing version, not the latest version. Stop when the cost limit is reached, evidence no longer improves, or required capability is unavailable.

### 9. Handoff honestly

Return the candidate path, frozen suite, run paths, report, remaining risks, and explicit claim limits. Keep report sections distinct:

- proven;
- disproven;
- unverified;
- not run;
- indicative observations, for weak text matches and optional judges;
- unobservable checks, for expectations the chosen host cannot see.

Say plainly which cases could not be judged and why. An `inconclusive` report caused by host limits is an honest result; do not present it as a candidate defect.

Do not infer installation, host activation, automatic routing, generalization, release readiness, or runtime safety from structure checks or explicit execution.

## Package a distribution

Build a host distribution from current source bytes into a new destination, then verify it:

```bash
python3 scripts/package.py build \
  --source <skill-forge-source> \
  --host <codex-or-claude> \
  --output <dist>/skill-forge \
  --manifest <dist>/skill-forge.manifest.json
python3 scripts/package.py verify \
  --candidate <dist>/skill-forge \
  --manifest <dist>/skill-forge.manifest.json \
  --output <dist>/skill-forge-receipt.json
```

The payload is `SKILL.md`, `VERSION`, `LICENSE`, `references/`, and `scripts/`, plus `agents/` for Codex. Fixtures, tests, and the packaging script itself are authoring inputs and never ship.

The receipt is a byte binding: it proves the shipped tree matches its manifest and that POSIX write bits were removed. It is not a signature, does not install anything, and does not prove the host loaded the Skill. A principal with write permission can rebuild any of it.

## Reauthorization boundaries

The explicit invocation authorizes read-only analysis and isolated candidate work inside the requested workspace. Ask again before:

- executing an untrusted validator or candidate script;
- exceeding the agreed paid-model or repetition budget;
- writing outside the requested workspace or to an external system;
- installing, overwriting, committing, or publishing.

Derive factual outcomes from evidence. Ask the user for values and permissions, not for whether a gate passed or which version won.
