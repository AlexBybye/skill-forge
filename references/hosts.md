# Host Execution

Use this reference before running Codex or Claude cases or making routing, isolation, activation, or artifact claims.

## Probe before use

Run each host's `--version` and relevant `--help` commands at execution time. Record the version and help digest. Help output proves that an interface flag is exposed; it does not prove authentication, automatic routing, isolation, model behavior, or host activation.

If a required flag is absent or a probe fails, record `not_run` and the missing capability. Do not substitute simulated success.

## Explicit execution

Skill Forge renders the bound Skill bytes into an explicit execution prompt. This measures behavior with the Skill supplied. It does not prove that the host discovered or automatically selected an installed Skill.

For response-only Codex cases, use a non-interactive, ephemeral, approval-free, read-only invocation with JSON output and user configuration/rules disabled. Capture the last message and raw JSON output outside the case workspace.

For response-only Claude cases, use print mode, JSON output, plan permission mode, no built-in tools, and no session persistence. This is a host tool policy, not an OS sandbox.

Treat command templates as version-sensitive recipes. Re-probe rather than assuming flags remain stable.

## Artifact-producing cases

Read-only or no-tools policies cannot test a task that must write an artifact. Run those cases only in a disposable fixture copy with a supported scoped-write policy.

The v1 runner supports scoped workspace writes for Codex. It does not implement Claude workspace writes because a stable, equivalently bounded allowlist has not been established. Record Claude artifact cases as unsupported instead of weakening the read-only policy.

Keep the original fixture outside the case workspace. Record initial artifacts, final artifacts, and created/changed/deleted paths. Exclude the staged Skill copy from task artifacts and verify that the bound Skill snapshot did not change.

## Routing

A routing pass requires both:

1. isolated injection of the candidate into the host's discovery surface without naming it in the prompt;
2. trustworthy telemetry identifying the selected Skill.

The v1 Codex and Claude explicit executors provide neither complete routing primitive. Routing cases therefore remain `not_run`. A behavioral difference without selection telemetry may be labelled a routing proxy in a future optional module, but it is not an automatic-routing pass.

A live no-Skill baseline also requires proof that the host cannot discover an installed Skill from its normal roots. The v1 executors do not establish that isolation, so live `no_skill` cases remain `not_run`. Fixture-host no-Skill runs test only pipeline semantics.

## Fixture host

Use the fixture host to test loader, runner, artifact, validator, matrix, and scorer behavior deterministically. Each case fixture may contain `response.candidate.json`, `response.baseline.json`, or `response.no_skill.json` plus an `input/` directory.

Fixture observations are synthetic. Reports must carry `fixture_host_only` and must not claim live model quality, host activation, or automatic routing.
