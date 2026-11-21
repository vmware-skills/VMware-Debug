<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware-Debug

<a id="readme-overview"></a>

把已有证据整理成更清晰的故障时间线。

[English](https://github.com/vmware-skills/VMware-Debug/blob/main/README.md) · 简体中文

社区维护 · MIT 开源 · 非 VMware/Broadcom 官方产品。

[![24 秒调查示意演示](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-zh.gif)](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-operations/vmware-skills-zh.mp4)

GIF 使用合成数据；视频包含脱敏的历史截图。[下载家族 AI 配音介绍（约 3 分钟）](https://github.com/vmware-skills/marketplace/releases/download/demos-2026-11-13-operations/vmware-skills-zh.mp4)

<a id="readme-capabilities-and-workflows"></a>

## 能解决什么问题？

- 关联离线事件与案例记录。
- 明确假设置信度与证据缺口。
- 获得有重点的下一步检查建议。

<a id="readme-quick-start"></a>

## 快速开始

安装已发布 CLI，并查看可用命令：

```sh
uv tool install vmware-debug==2.4.0
vmware-debug --help
```

需要 uv 与 Python >=3.10。案例证据与审计数据保存在本机，无需连接 VMware API。[配置指南](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/setup-guide.md)。

完成本地配置后可一键安装：[Claude Desktop 扩展（.mcpb）](https://github.com/vmware-skills/VMware-Debug/releases/download/v2.4.0/vmware-debug-2.4.0.mcpb) · [在 VS Code 中安装](https://insiders.vscode.dev/redirect/mcp/install?name=vmware-debug&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22vmware-debug%3D%3D2.4.0%22%2C%22vmware-debug-mcp%22%5D%7D)。两者运行同一个固定的 2.4.0 版本。桌面扩展默认只提供只读工具，需要写操作时把“Tools to offer”改为 `all`。

Agent 宿主安装见本模块的[配置指南](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/setup-guide.md)，以及 [Cursor、Codex、Gemini CLI、Cline 与本地模型示例](https://github.com/vmware-skills/marketplace/blob/main/docs/agent-hosts-CN.md)。WorkBuddy 用户可在 [SkillHub](https://skillhub.cn) 搜索 `vmware-debug` 安装技能，并按 [WorkBuddy 说明](https://github.com/vmware-skills/marketplace/blob/main/docs/agent-hosts-CN.md#workbuddy) 注册 MCP。

选模型可用[共享模型自测流程](https://github.com/vmware-skills/marketplace/blob/main/model-fit/RUNNER-CN.md)：统一任务、复核质量与安全，再按任务组选择最小已测通过配置；不代表生产质量保证。

<a id="readme-safety-and-audit"></a>

## 使用边界

CLI/MCP 分析工具不连接 VMware API，也不执行修复。本地保存案例与审计数据；假设不等于已证实根因。可选 Python 宿主辅助模块通过已有守卫调用显式提供的 companion MCP 会话，不管理端点凭据，也不绕过后端审计。

<a id="readme-mcp-configuration"></a> <a id="readme-troubleshooting"></a> <a id="readme-reference"></a>

## 详细文档

- [能力范围](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/capabilities.md)
- [CLI 用法](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/cli-reference.md)
- [Agent 使用约束](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/agent-guardrails.md)
- [详细用法与架构](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/detailed-guide-CN.md)
- [MCP 启动配置](https://github.com/vmware-skills/VMware-Debug/blob/main/.mcp.json)
- [唯一技能指令正本](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/SKILL.md)

<a id="readme-companion-skills"></a>

属于 [VMware Skills 家族](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)：15 个 skill、14 个 MCP server 与一个共享 Policy 库。如果对你有用，欢迎 Star 家族门户。

<a id="readme-license"></a>

[问题与反馈](https://github.com/vmware-skills/VMware-Debug/issues) · [MIT](https://opensource.org/license/mit/)

## 场景调查与报告

可选 Python 宿主辅助模块可选择实时场景 schema、绑定已连接的只读 MCP 会话并共享单进程调用预算。见[宿主集成与限制](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/scenario-host-CN.md)。不宣称官方 AI Hub 兼容或 token 节省。


只读[VM 性能异常](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/vm-performance-CN.md)与[主机告警](https://github.com/vmware-skills/VMware-Debug/blob/main/skills/vmware-debug/references/host-alarms-CN.md)流程定义证据、分支和停止条件。get_case 的可选 report_view 提供简报/运维详情/调查记录，复用现有账本。已通过有限实验室只读/报告检查；完整自主采集、受控 token 对照和官方 MCP 环境验证仍为后续待办。
