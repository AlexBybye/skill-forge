# Risk

Use this reference when candidate behavior or evaluation can cause side effects. Derive controls from behavior, not Skill length or validation budget.

## Risk dimensions

| Dimension | Trigger | Minimum response |
|---|---|---|
| `local_mutation` | Writes or changes local files | Isolated fixture, deterministic artifact check, cleanup or recovery plan |
| `external_write` | Writes to an API, cloud, calendar, message system, or remote repo | Exact target and scope, least privilege, dry run when available, explicit authorization |
| `irreversible_change` | Delete, publish, rotate, charge, or destructive migration | Recoverable snapshot, rehearsal, pre-action verification, fail closed |
| `credential_or_sensitive_data` | Tokens, secrets, private content, personal data | Minimal exposure, sanitized environment, redaction, bounded retention |
| `shared_trigger` | Skill can trigger for other users or adjacent tasks | Near negatives, adjacent-Skill regression, false-trigger evidence |
| `real_external_dependency` | Network service, host CLI, mutable schema, paid model | Current interface probe, timeout, recovery, version recording, degradation path |
| `high_impact_evaluation` | Evaluator itself can write, execute, or affect production | Disposable environment, production-write denial, timeout, output cap, cleanup verification |

If the required control is unavailable, reduce the claim, use design-only output, or stop. Never simulate a passing control.

## Authorization boundary

Explicit invocation covers read-only analysis and isolated candidate writes inside the requested workspace. Obtain separate approval for:

- untrusted candidate or validator execution;
- paid calls beyond the agreed budget;
- workspace-external or external-system writes;
- installation, overwrite, commit, publication, or irreversible action.

Record the exact action and target in the request. Do not build grants, nonces, expiry chains, or authorization reducers into this Skill.

## Static risk hints

Use Python AST checks to identify high-signal operations such as dynamic code, subprocess execution, `shell=True`, destructive filesystem calls, network imports, and network calls.

Treat the output as:

- `findings`: observed patterns that should influence controls and tests;
- `unknowns`: syntax failures, dynamic indirection, unsupported languages, or unscanned behavior;
- `not_scanned`: absent tooling or intentionally excluded resources.

Never translate “no finding” into “safe”. Static analysis cannot observe dynamic inputs, host tools, indirect imports, external state, or runtime permission boundaries.

## Validators

Prefer built-in typed checks. When a custom validator is necessary:

1. inspect and explicitly trust the exact file;
2. pass an argv array without a shell;
3. use a bounded timeout and output capture;
4. run in the disposable case workspace;
5. record its SHA-256 and raw stdout/stderr;
6. detect artifact mutation by the validator;
7. classify timeout, drift, or missing authorization as incomplete evidence.

A validator pass proves only its declared observation for the bound artifact. It does not certify candidate runtime safety.
