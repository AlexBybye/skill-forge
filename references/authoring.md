# Authoring

Use this reference for need analysis, goal framing, candidate creation, and causal revision.

## Need gate

Choose `reuse` when an available Skill already covers the target behavior and boundaries. Choose `no_skill` only when a real candidate-free baseline can be tested. A failed candidate does not prove that no Skill is needed.

Use `create` for a new capability and `optimize` for a concrete failure in an existing Skill. Optimization requires an immutable baseline.

## Independent goal card

Before reading a proposed implementation as the answer, freeze:

1. target user and task;
2. observed problem or repeated work;
3. desired behavior;
4. explicit non-goals;
5. evidence sources;
6. acceptance cases;
7. relevant quality dimensions;
8. effort limit and stop reason.

Compare designs only after independently deriving the frame. Use the same dimensions for every proposal: goal fit, evidence, expected benefit, cost, risk, reversibility, and validation method.

## Requirements handoff

When cases and candidate are written by separate agents, `requirements.md` is the only thing that crosses the boundary. Include:

- goal, target user, and the real task;
- observed failures and repeated work, with source labels;
- desired behavior and explicit non-goals;
- constraints the user stated;
- acceptance intents in plain language;
- open questions still unresolved.

Exclude implementation choices, file layouts, script designs, and phrasing suggestions. A case agent that can see the intended implementation writes cases shaped like it, which is the failure the separation exists to prevent.

Whatever is missing here is lost: the build agent cannot recover context from a conversation it never saw. Review this file with the user before spawning either subagent.

## Source labels

Label every authoring example and claim source:

- `observed`: a raw trace, artifact, test, or current repository fact;
- `user_confirmed`: the user explicitly confirmed the fact or desired behavior;
- `synthetic`: created to exercise a boundary or failure mode;
- `assumed`: plausible but not yet verified.

Synthetic and assumed examples may guide design. They do not prove prevalence, representativeness, or generalization.

## Quality dimensions

Select only dimensions relevant to the goal:

- necessity;
- routing and trigger precision;
- execution correctness;
- boundaries and near negatives;
- content efficiency;
- stability and determinism;
- maintainability;
- security and permissions.

Do not combine these into one overall quality score. A critical regression must not be hidden by averages elsewhere.

## Content admission

Keep content only when it is not reliably inferable, fixes an observed failure or high-risk case, captures host/project/domain specificity, prevents repeated work, or is required by a real test.

Prefer a concrete example over a paragraph of generic explanation. Trace precautions to an observation, reliable source, or explicitly labelled high-risk assumption. Do not repeat a guess until it appears factual.

Keep `SKILL.md` concise and imperative. The description must say what the Skill does and when it applies. Avoid a separate “when to use” body section because triggering occurs before the body is loaded.

## Identity versus location

`frontmatter.name` is the Skill's install identity: hosts discover a Skill by a directory whose name equals it. That constraint applies at the install target, not while authoring. Keep the candidate in an isolated directory with any convenient name and let the frozen suite's `skill` field carry the identity, or pass `--expect-name` to the checker.

## Resource placement

- `SKILL.md`: persistent workflow, decision rules, stop conditions, and reference routing.
- `references/`: conditional detail loaded only when needed; keep one level deep.
- `scripts/`: fragile or repeatedly rewritten deterministic operations; test success and failure paths.
- `assets/`: resources copied or consumed in real outputs.
- development fixtures: keep outside the runtime distribution.
- reports, caches, research notes, placeholders, and unused directories: delete.

## Causal optimization

1. Snapshot the baseline without following symlinks.
2. Reproduce the original failure from observed or user-confirmed evidence.
3. Attribute it to routing, execution, resources, efficiency, adapter behavior, or evaluation design.
4. Change one causal hypothesis or atomic change set.
5. Re-run the failure and affected regressions.
6. Compare the complete frozen case matrix.
7. Keep the baseline when no gain is shown.
8. Preserve the best passing candidate instead of the newest candidate.

Do not let an authoring ledger, model confidence, or user preference declare quality gain. Derive gain from the selected cases and state the scope of that conclusion.
