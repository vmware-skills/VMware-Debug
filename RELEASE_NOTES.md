## v1.8.1 (2026-07-19) — read-only mode reaches the surfaces that teach it

v1.8.0 put read-only mode in the code and documented it in the README only.
Every other layer was empty, and each serves a different reader: SKILL.md is what
the agent loads, setup-guide is what an operator reads while configuring, `doctor`
is where they verify it took. The gap had two concrete costs.

An agent read SKILL.md, called a write tool the gate had withheld, and got nothing
back — with no way to learn that the absence was a deliberate lockdown rather than
a fault. It reads as a broken tool, so the model retries or hunts for a workaround.

An operator who set the switch had no way to confirm it. The only signal was a line
in the MCP server's start-up log.

### Added — the feature is now documented where each reader looks

SKILL.md, setup-guide and capabilities now cover read-only mode. Both tools here
are reads, so nothing is withheld — the gate instead verifies that at start-up
rather than taking this documentation's word for it. Env vars are the only switch;
this package has no config file.

## v1.8.0 (2026-07-18) — read-only mode, working policy defaults, declared environments

Family release driven by [VMware-AIops#31](https://github.com/zw008/VMware-AIops/issues/31),
where an operator running Llama 3.3 70B (Goose / OpenShift AI, on-prem H100) had to
hand-write 17 prompt guardrails to make tool calling reliable. A prompt is advisory — a
model can ignore it. Every guardrail that could move into the harness has.

### Added
- **Read-only mode.** Set `VMWARE_READ_ONLY=true` (or the per-skill
  `VMWARE_DEBUG_READ_ONLY`) and every write tool is removed from the MCP registry at
  start-up. `list_tools()` never offers them, so the model cannot call what it cannot
  see. **Off by default** — nothing changes unless you turn it on. Fail-closed: if the
  mode is requested but cannot be guaranteed, the server refuses to start rather than
  running open. **vmware-debug has no `config.yaml`, so the environment variables are the
  only switch** — precedence is per-skill env → family env → off. Both tools are `[READ]`,
  so the gate withholds nothing here; its empty result *is* the assertion.
- **`environment:` scoping for policy rules.** Policy rules now scope by the environment
  a target declares (production / staging / lab). Skills that connect to a VMware estate
  declare it per target in their own `config.yaml`; vmware-debug connects to nothing and
  has no config, so it reports a constant `local`.

### Added — list results now state whether they are complete

Every `[READ]` list tool returns the family envelope instead of a bare array:

    {"items": [...], "returned": 50, "limit": 50, "total": 213,
     "truncated": true, "hint": "Showing 50 of 213. Raise limit or narrow the query..."}

This closes the reported failure where long responses were summarised as "no data
returned": a bare list gives a model no way to tell a complete answer from page one, so
it guessed. `truncated: false` now positively states completeness — including when
`items` is empty, which means "checked, found none", not "the call failed".

- **1 tool(s) converted** across ops, MCP and CLI. The routing table is a fixed constant, so `total` is exact and `truncated` is always
  false — there is no page two to go looking for.

### Changed — migration, read this
- **Approval tiers now actually run.** They shipped in v1.6.0 but the engine only ever
  read `~/.vmware/rules.yaml`, and a fresh install has no such file — so every deny rule,
  maintenance window and approval tier had been inert on every install that never
  hand-authored one. A packaged baseline now loads when you have written no rules of your
  own. Writes at medium risk and above are stamped with their tier in the audit log;
  irreversible work and guest execution against a target declared `production` require a
  named approver via `VMWARE_AUDIT_APPROVED_BY`.
- **`environment:` will become required for writes — but not here.** Across the family, a
  state-changing operation against a target that declares no `environment:` still runs and
  logs a warning, and **the next major release refuses it**. vmware-debug ships no tool
  above read risk, holds no credentials and makes no network calls, so there is nothing to
  migrate — it reports a constant `local` and the upgrade is a no-op for this package.
  Read-only operations are never affected, in this release or the next. Check what applies
  to your write-capable siblings before upgrading:
  `vmware-audit policy --operation vm_delete --env <env>`.

### Fixed
- **Policy glob patterns with a leading wildcard silently matched nothing.** A rule written
  `operations: ["*_delete"]` parsed fine, read correctly, and never fired — only a trailing
  `*` was honoured. Now full glob matching, for operations and environments alike.

### Notes
- Requires `vmware-policy>=1.8.0`; publish that package first.
- `vmware-audit policy` reports which rules are in force and where they came from —
  including the case where your rules file exists but failed to parse, which previously
  looked identical to "policy is working".

## v1.6.1 (2026-06-24) — initial release

First release of **vmware-debug**: the read-only diagnostic brain of the VMware
skill family. You bring the symptom; it runs the investigation, correlates
events from the other skills into one timeline, ranks root-cause hypotheses, and
routes remediation to vmware-aiops / vmware-pilot. It never writes and never
executes fixes (advisor/executor split, mirroring vmware-harden → vmware-pilot).

### Added
- **2 read-only MCP tools**: `incident_timeline` (correlate pre-fetched events
  into a timeline + z-score spikes + ranked hypotheses + next-check ideas) and
  `list_symptom_categories` (the symptom→skill routing catalogue).
- **Unified event envelope** + tolerant normalizer so debug stays source-agnostic
  with zero runtime dependency on the other skill packages — the agent fans out
  to each skill's read tools and feeds events here (avoids cross-skill coupling,
  踩坑 #21/#32).
- **Pure correlation engine** (timeline merge, time-binning, spike detection,
  hypothesis ranking, symptom routing) — fully unit-tested offline.
- **Typer CLI**: `triage`, `categories`, `version`, `mcp`. The `mcp` entry point
  needs no network at startup (proxy-safe, 踩坑 #25).
- SKILL.md + references (event-envelope contract, symptom routing, playbooks).

### Notes
- Read-only by construction; remediation is routed, never executed.
- `parse_timestamp` rejects implausible/garbage timestamps loudly rather than
  silently landing at the epoch.
