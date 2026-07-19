# vmware-debug Setup Guide

vmware-debug has **no configuration, no credentials, and no network access** — it
is a pure, offline correlation engine. There is no `config.yaml` and no `.env`.

## Install

```bash
uv tool install vmware-debug
vmware-debug categories      # verify it runs
```

## MCP client configuration

```json
{
  "command": "uvx",
  "args": ["--from", "vmware-debug", "vmware-debug-mcp"]
}
```

If installed with `uv tool install`, prefer the entry point `vmware-debug mcp`
(no PyPI resolution at startup — robust behind corporate TLS proxies, 踩坑 #25).

For full cross-skill diagnosis, also install the data-source skills it correlates
(vmware-monitor, vmware-log-insight, vmware-aria, vmware-nsx) and the executors it
routes fixes to (vmware-aiops, vmware-pilot).

## Read-Only Mode

Off by default. Both of debug's tools are reads, so turning it on withholds nothing here —
the value is that the gate *verifies* at start-up that no write tool is exposed, instead of
trusting this document, and that the same family variable locks down every other installed
skill in one setting.

**Debug has no config file, so the environment is the only switch.** There is no
`read_only:` setting to write. Precedence:

| Priority | Signal | Scope |
|---|---|---|
| 1 | `VMWARE_DEBUG_READ_ONLY` | This skill only |
| 2 | `VMWARE_READ_ONLY` | Every installed VMware skill |
| 3 | (nothing set) | Off |

```json
{
  "mcpServers": {
    "vmware-debug": {
      "command": "vmware-debug",
      "args": ["mcp"],
      "env": { "VMWARE_READ_ONLY": "true" }
    }
  }
}
```

**Fail-closed.** If the mode is requested but cannot be *proven*, the server refuses to
start with `ReadOnlyGateError`: the FastMCP tool registry cannot be enumerated (usually an
incompatible `mcp` version), or a removal did not take effect. One case does *not* abort —
an unparseable value (`VMWARE_DEBUG_READ_ONLY=ture`) resolves to **on** with a warning
naming the accepted values, so a typo locks the deployment down rather than leaving it
open.

**Verifying.** Debug ships no `doctor` command; the start-up log is the record — the server
logs every withheld tool when the mode engages, and logs nothing to withhold here. Skills
that do ship one (e.g. `vmware-log-insight doctor`) report their resolved state and where it
came from.

## Security

> **Disclaimer**: Community-maintained open-source project, **not affiliated with,
> endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.**

1. **Source Code** — https://github.com/zw008/VMware-Debug (MIT).
2. **Credentials** — none. debug holds no secrets and connects to nothing.
3. **Network** — none. All tools are local pure functions over event data the
   agent supplies.
4. **Writes** — none. debug only diagnoses and recommends; remediation is routed
   to vmware-aiops / vmware-pilot, where confirmation/approval/audit live.
5. **No cross-skill coupling** — events arrive as plain dicts (the event
   envelope); debug imports no other skill package at runtime.
6. **Environment scoping** — policy rules scope by environment, and skills that
   connect to a VMware estate declare `environment:` (`production` / `staging` /
   `lab`) per target in their own `config.yaml`; a target that declares none is
   treated as unknown, and state-changing operations against it currently log a
   warning — the next major release will refuse them. debug has no config and
   no connection to declare one about, so it reports a constant `local`. Since
   it ships no operation above read risk, nothing here is gated either way.
7. **Static analysis** — `uvx bandit -r vmware_debug/` (release bar:
   0 Medium+).
