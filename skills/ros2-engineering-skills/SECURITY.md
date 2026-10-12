# Security policy

## Supported version

Security fixes target the current default branch. Older revisions may not
receive backports.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose credentials,
permit unsafe command execution, or materially weaken a robot safety boundary.
Send a concise report to `yujinhong3@gmail.com` with:

- affected revision and file;
- impact and prerequisites;
- minimal reproduction;
- suggested mitigation, when known;
- whether the report involves physical hardware.

Please avoid including production secrets, private repositories, personal data,
or uncontrolled hardware procedures.

## Scope

The command and source validators are best-effort development aids. They are not
security sandboxes, authorization systems, or certified safety mechanisms.
Regex-based command checks can be bypassed by shell expansion, aliases,
substitution, or alternate tools and must never be the only protection around a
destructive operation.
