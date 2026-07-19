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

- **Read-only by design — and provable** (v1.8.0): both MCP tools are read, none write; set `VMWARE_READ_ONLY=true` (or the per-skill `VMWARE_DEBUG_READ_ONLY`) and the family read-only gate verifies that at startup instead of taking the docs' word for it — env vars are the only switch here, this skill has no config file. See [Read-Only Mode](#read-only-mode).

## MCP tools

| Tool | What |
|---|---|
| `incident_timeline` | [READ] Correlate pre-fetched events → timeline + spikes + ranked hypotheses + next-check ideas |
| `list_symptom_categories` | [READ] List recognised symptom categories + what to check for each |

## Read-Only Mode

vmware-debug is read-only by design — both MCP tools carry the `[READ]` marker, take no
credentials, and make no network calls at all; they only correlate event dicts the
calling agent has already fetched with the other skills' read tools. Since v1.8.0 that
is **provable rather than merely documented**: set `VMWARE_READ_ONLY=true` and the
family read-only gate enumerates the registry at startup and verifies that zero write
tools are exposed — structural, not a prompt instruction a model can ignore. **Off by
default.** Fail-closed: if the mode is requested but cannot be guaranteed, the server
refuses to start rather than running open.

The same variable is family-wide: one env var also strips every write tool from the
write-capable siblings (aiops, storage, vks, nsx, ...), so a whole-estate read-only
posture is a single setting.

```json
{
  "mcpServers": {
    "vmware-debug": {
      "command": "vmware-debug",
      "args": ["mcp"],
      "env": {
        "VMWARE_READ_ONLY": "true"
      }
    }
  }
}
```

- **Per-skill override**: `VMWARE_DEBUG_READ_ONLY` beats the family-wide
  `VMWARE_READ_ONLY`. vmware-debug has no `config.yaml`, so the env vars are the only
  switch. Precedence: per-skill env → family env → off.
- **Classification**: this skill registers its tools through a `build_server()` factory,
  so the gate classifies from the `[READ]`/`[WRITE]` docstring marker rather than from
  MCP annotations. Anything not provably read-only is treated as a write.
- **Startup log**: nothing is logged as withheld because nothing is — the gate's empty
  result *is* the assertion (write-capable siblings log
  `Read-only mode active ... withheld N write tool(s)` instead).

## License

MIT.
