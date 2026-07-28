# Evaluation

Use this reference to design suites, distinguish evaluation planes, and derive bounded decisions.

## Freeze before observation

Freeze cases, prompts, fixtures, critical flags, typed expectations, repetitions, and comparison rules before generating or judging a candidate. Keep the suite candidate-neutral. Pass baseline and candidate paths at runtime instead of storing them in the suite.

Changing the suite after observing results starts a new experiment. Preserve the previous run instead of silently rewriting its expectations.

## Case design

Cover the smallest set that tests the claimed behavior:

- `core`: the main real task;
- `boundary`: valid edge conditions;
- `failure`: expected error handling;
- `near_negative`: adjacent tasks where the Skill should not trigger or should refuse scope.

Critical execution cases require at least one strong deterministic expectation: exact stdout, file digest, JSON equality, or an explicitly trusted validator. `contains`, exit code, and file existence may supplement a gate but cannot be the only strong evidence for a critical behavior.

## Separate planes

Routing cases measure whether a host selects the Skill without naming it. They require both isolated candidate injection and trustworthy selected-Skill telemetry. If either capability is missing, record `not_run`; never infer selection from answer wording.

Execution cases explicitly supply the Skill and test task behavior. Explicit execution proves neither automatic routing nor host discovery.

Response-only cases may use read-only host policies. Cases that create or modify artifacts require a disposable fixture workspace and a supported scoped-write policy. Do not run a write case under a no-tools/read-only policy and call the absence of output a Skill failure.

## Baselines

For `optimize`, run the identical suite against an immutable baseline and candidate. Keep the complete `case × configuration × repetition` matrix.

For `no_skill`, run a configuration with no candidate distribution or embedded Skill. A candidate that fails to improve is not a no-Skill baseline.

For `create` without a baseline, passing cases support candidate handoff only. They do not prove improvement.

## Statistical unit

Use case as the statistical unit. Repetitions measure within-case stability and do not create extra case weight. Exclude incomplete pairs and infrastructure errors from gain counts; classify the decision as inconclusive when required evidence is incomplete.

Do not turn criterion count, judge count, or output lines into extra votes.

## Development and holdout

Keep development examples visible to the authoring loop. A holdout requires separate access control and must remain unseen during authoring and revision. Renaming or moving a development case does not make it unseen.

Enable holdout only when a generalization claim justifies the added machinery. Otherwise state that selected development cases were tested.

## Decision rules

For create:

- all critical candidate cases complete without failure: `handoff_candidate`;
- a critical candidate failure: `reject_candidate`;
- missing or unsupported critical evidence: `inconclusive`.

For optimize:

- at least one case improves and no critical case regresses: `adopt_candidate_for_selected_cases`;
- no improvement or any critical regression: `keep_baseline`;
- incomplete baseline/candidate matrix: `inconclusive`.

For no-Skill:

- all critical candidate-free cases pass: `no_skill_supported_for_selected_cases`;
- a critical case fails: `no_skill_not_supported_for_selected_cases`;
- required evidence is incomplete: `inconclusive`.

No decision implies installation, release, automatic routing, host activation, long-term stability, or safety.
