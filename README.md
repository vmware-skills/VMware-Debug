<!-- mcp-name: io.github.zw008/vmware-debug -->

# VMware Debug

> ⚠️ **Work in progress** — the core (event correlation engine, MCP tools, CLI)
> is built and tested; README, `server.json`, full reference docs, and packaging
> polish are still landing. Not yet published to PyPI.

> **Disclaimer**: Community-maintained open-source project, **not affiliated with,
> endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and
> "vSphere" are trademarks of Broadcom. Source is publicly auditable under the MIT
> license.

The diagnostic brain of the VMware skill family. You bring the symptom (an error,
a log dump, a slow VM); this skill runs a systematic investigation, correlates
events from the other skills into one timeline, ranks root-cause hypotheses, and
tells you what to check next. It is **read-only** — it never changes anything and
never executes fixes. Remediation is routed to `vmware-aiops` (single op) or
`vmware-pilot` (multi-step, gated), mirroring the `vmware-harden → vmware-pilot`
advisor/executor split.

See [`skills/vmware-debug/SKILL.md`](skills/vmware-debug/SKILL.md) for the full
methodology, the event-envelope contract, and symptom routing.

## MCP tools

| Tool | What |
|---|---|
| `incident_timeline` | [READ] Correlate pre-fetched events → timeline + spikes + ranked hypotheses + next-check ideas |
| `list_symptom_categories` | [READ] List recognised symptom categories + what to check for each |

## License

MIT.
