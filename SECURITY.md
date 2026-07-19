# Security Policy

## Disclaimer

This is a community-maintained open-source project and is **not affiliated with,
endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and
"vSphere" are trademarks of Broadcom. Source code is publicly auditable at
[github.com/zw008/VMware-Debug](https://github.com/zw008/VMware-Debug) under the
MIT license.

## Reporting Vulnerabilities

Report security issues via a GitHub private security advisory on the repository,
or by email to the maintainer. Please do not open public issues for security bugs.

## Security Design

### Read-only and offline by construction
vmware-debug has **no write tools, no network access, and no credentials**. It
does not connect to vCenter, NSX, Aria, or any appliance. Its tools are pure
functions over event data the orchestrating agent has already fetched with the
other skills' read tools. There is no destructive surface and no secret to leak.

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
uvx bandit -r vmware_debug/
```

Release bar: 0 Medium-or-higher severity findings.

## Supported Versions

The latest released version receives fixes. Versions are kept aligned across the
VMware skill family.
