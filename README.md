<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware-Debug

<a id="readme-overview"></a>

Build a clearer incident timeline from evidence you already have.

English · [简体中文](https://github.com/vmware-skills/VMware-Debug/blob/main/README-CN.md)

Community-maintained · MIT · Not an official VMware/Broadcom product.

[![24-second illustrative investigation demo](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-en.gif)](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-model-fit/vmware-skills-en.mp4)

Synthetic-data GIF; video includes redacted historical screenshots. [Download the AI-narrated family introduction (~3 min)](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-model-fit/vmware-skills-en.mp4)

<a id="readme-capabilities-and-workflows"></a>

## Why use it?

- Correlate offline events and case records.
- Rank hypotheses with explicit confidence and gaps.
- Get focused next-check recommendations.

<a id="readme-quick-start"></a>

## Get started

Install the published CLI and inspect its commands:

```sh
uv tool install vmware-debug==2.3.3
vmware-debug --help
```

Requires uv and Python >=3.10. Keep case evidence and audit data locally; no VMware API connection is needed. [Setup guide](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/setup-guide.md).

For agent-host installation, use the [setup guide](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/setup-guide.md) and [Cursor, Codex, Gemini CLI, Cline and local-model examples](https://github.com/vmware-skills/marketplace/blob/main/docs/agent-hosts.md).

Choose a model with the [shared model-fit workflow](https://github.com/vmware-skills/marketplace/blob/main/model-fit/RUNNER.md): compare the same tasks, review quality and safety, then select the smallest tested passing configuration per task group; this is not a production guarantee.

<a id="readme-safety-and-audit"></a>

## Know the boundaries

Offline analysis: no VMware API connection or remediation execution. Case records and audit data are stored locally; hypotheses are not established root causes.

<a id="readme-mcp-configuration"></a> <a id="readme-troubleshooting"></a> <a id="readme-reference"></a>

## Go deeper

- [Capabilities](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/capabilities.md)
- [CLI reference](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/cli-reference.md)
- [Agent guardrails](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/agent-guardrails.md)
- [Detailed usage and architecture](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/detailed-guide.md)
- [MCP launch configuration](https://github.com/vmware-skills/VMware-Debug/blob/main/.mcp.json)
- [Canonical skill instructions](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/SKILL.md)

<a id="readme-companion-skills"></a>

Part of [VMware Skills](https://github.com/vmware-skills/marketplace): 15 skills, 14 MCP servers and one shared Policy library. If it helps your work, star the family portal.

<a id="readme-license"></a>

[Issues and feedback](https://github.com/vmware-skills/VMware-Debug/issues) · [MIT](https://opensource.org/license/mit/)
