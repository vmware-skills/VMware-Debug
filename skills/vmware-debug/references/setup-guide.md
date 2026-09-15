# vmware-debug Setup Guide

vmware-debug has **no configuration, no credentials, and no network access** — it
is a pure, offline correlation engine. There is no `config.yaml` and no `.env`.

## Install

```bash
uv tool install vmware-debug==1.12.1
vmware-debug categories      # verify it runs
```

## MCP client configuration

```json
{
  "command": "uvx",
  "args": ["--from", "vmware-debug==1.12.1", "vmware-debug-mcp"]
}
```

If installed with `uv tool install`, prefer the entry point `vmware-debug mcp`
(no PyPI resolution at startup — robust behind corporate TLS proxies, 踩坑 #25).

For full cross-skill diagnosis, also install the data-source skills it correlates
(vmware-monitor, vmware-log-insight, vmware-aria, vmware-nsx) and the executors it
routes fixes to (vmware-aiops, vmware-pilot).

## Security

> **Disclaimer**: Community-maintained open-source project, **not affiliated with,
> endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.**

1. **Source Code** — https://github.com/vmware-skills/VMware-Debug (MIT).
2. **Credentials** — none. debug holds no secrets and connects to nothing.
3. **Network** — none. The correlation tools are local pure functions over
   event data the agent supplies; the case tools read and write local files.
4. **Writes** — only to the local investigation ledger under `$OPS_HOME`
   (the seven [WRITE] `case_*` tools). Evidence, gaps, hypotheses and grade
   history are only ever added to; `timeline.md` is regenerated from the evidence and
   `case.json` holds the current grade and state. Nothing is written to any
   VMware system: debug only diagnoses and recommends; remediation is routed
   to vmware-aiops / vmware-pilot, where confirmation/approval/audit live.
   Case data is sensitive: each case folder (`$OPS_HOME/cases/<case-id>/`, default
   `~/.vmware/cases/`) is created owner-only (`0700`) and holds the submitted
   evidence — host names, addresses, log and event text. There is no automatic
   retention limit: `case_close` marks a case closed and keeps its files; delete
   the folder to remove a case.
5. **No cross-skill coupling** — events arrive as plain dicts (the event
   envelope); debug imports no other skill package at runtime.
6. **Environment scoping** — policy rules can scope by environment, and skills
   that connect to a VMware estate may declare `environment:` (`production` /
   `staging` / `lab`) per target in their own `config.yaml` as an optional label
   an environment-scoped `deny` rule can match on. debug has no config and no
   connection to declare one about, so it registers no environment resolver —
   it has no basis to answer for any target. Its tools do not pass through the
   policy engine either, so no policy rule, environment-scoped or not, applies
   to them.
7. **Static analysis** — `uvx bandit -r vmware_debug/` (release bar:
   0 Medium+).
