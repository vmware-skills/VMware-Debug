# Security Policy

## Disclaimer

This is a community-maintained open-source project and is **not affiliated with,
endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and
"vSphere" are trademarks of Broadcom. Source code is publicly auditable at
[github.com/vmware-skills/VMware-Debug](https://github.com/vmware-skills/VMware-Debug) under the
MIT license.

## Reporting Vulnerabilities

Report security issues via a GitHub private security advisory on the repository,
or by email to the maintainer. Please do not open public issues for security bugs.

## Security Design

### No VMware API access; local ledger and audit writes
The diagnostic tools make no VMware API calls and need no VMware account credentials.
Case operations write a local ledger under `$OPS_HOME`; every tool also records a
separate audit row in `~/.vmware/audit.db`. Default stdio starts no listener.
Opt-in authenticated HTTPS opens the configured listener and requires caller tokens
and TLS certificate/key files, which must be protected as secrets. Case records may
contain sensitive operational data; redact input and apply a local retention policy.

### No remediation execution
debug only *diagnoses* and *recommends*. Any fix is routed to vmware-aiops
(single op, with its own confirmation) or vmware-pilot (multi-step, approval-gated,
audited). The safety gates live in those skills, not here.

### No cross-skill coupling
debug imports none of the other skill packages at runtime. Events arrive as plain
dicts (the unified event envelope), so there is no transitive dependency surface.

### Prompt-injection consideration
debug operates on text the agent supplies. Its outputs are structured data
(timelines, hypotheses, routing strings); it does not execute or shell out to
anything based on event content.

## Static Analysis

```bash
uvx bandit==1.9.4 -r vmware_debug/
```

Release bar: 0 Medium-or-higher severity findings.

## Supported Versions

The latest released version receives fixes. Versions are kept aligned across the
VMware skill family.
