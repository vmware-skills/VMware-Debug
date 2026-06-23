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
