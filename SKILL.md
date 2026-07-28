---
name: skill-forge
description: Create, audit, or improve reusable Codex and Claude Skills through content admission, candidate-neutral task suites, immutable baselines, raw observations, and bounded evidence claims. Use when explicitly invoked with $skill-forge to decide whether a Skill is needed, create a small Skill, optimize an existing Skill, or test whether no Skill is sufficient.
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

Choose exactly one mode:

- `reuse`: use an adequate adjacent Skill; do not create a duplicate.
- `create`: create a candidate without claiming gain unless a real no-Skill comparison exists.
- `optimize`: preserve an immutable baseline and improve one observed failure.
- `no_skill`: create no candidate and run a candidate-free baseline probe.

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

### 3. Freeze the suite

Create one strict `suite.json` before generating or observing the candidate. Keep baseline and candidate paths out of the suite; pass them to the runner.

Include real core cases plus relevant boundary, failure, and near-negative cases. Mark every case with `observed`, `user_confirmed`, `synthetic`, or `assumed`. Treat development cases as development cases; do not relabel them as holdout.

Read [references/evaluation.md](references/evaluation.md) for suite and decision rules. Read [references/evidence.md](references/evidence.md) before authoring `suite.json` or interpreting a report.

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

Use `fixture` only to test the pipeline itself; it cannot prove live model or host behavior. Do not run a custom validator unless the user trusts it and its exact relative path is passed with `--allow-validator`.

Read [references/hosts.md](references/hosts.md) before live host execution. Do not spend paid-model budget or enable writes beyond the agreed scope implicitly.

### 7. Score without executing

Reduce completed run directories:

```bash
python3 scripts/score.py \
  --suite <suite.json> \
  --run <baseline-run> \
  --run <candidate-run> \
  --output-dir <new-report-dir>
```

`score.py` must derive results from raw observations. Never add caller-authored `passed`, `winner`, or terminal facts to `results.jsonl`.

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
- indicative observations, when an optional judge was used.

Do not infer installation, host activation, automatic routing, generalization, release readiness, or runtime safety from structure checks or explicit execution.

## Reauthorization boundaries

The explicit invocation authorizes read-only analysis and isolated candidate work inside the requested workspace. Ask again before:

- executing an untrusted validator or candidate script;
- exceeding the agreed paid-model or repetition budget;
- writing outside the requested workspace or to an external system;
- installing, overwriting, committing, or publishing.

Derive factual outcomes from evidence. Ask the user for values and permissions, not for whether a gate passed or which version won.
