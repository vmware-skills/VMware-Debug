<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware-Debug

把已有证据整理成更清晰的故障时间线。

English · [简体中文](README-CN.md)

社区维护 · MIT 开源 · 非 VMware/Broadcom 官方产品。

[![18 秒调查示意演示](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-zh.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

使用合成数据的示意动画，非实时录屏。[观看家族配音介绍（约 2 分钟）](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

## 能解决什么问题？

- 关联离线事件与案例记录。
- 明确假设置信度与证据缺口。
- 获得有重点的下一步检查建议。

## 快速开始

安装已发布 CLI，并查看可用命令：

```sh
uv tool install vmware-debug==2.3.3
vmware-debug --help
```

需要 uv 与 Python >=3.10。远程查询前，在本地配置目标与凭据。[配置指南](skills/vmware-debug/references/setup-guide.md).

Claude Code 及其他宿主安装方式见[家族门户](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)。

## 使用边界

离线分析，不连接 VMware API，也不执行修复。本地保存案例与审计数据；假设不等于已证实根因。

## 详细文档

- [能力范围](skills/vmware-debug/references/capabilities.md)
- [CLI 用法](skills/vmware-debug/references/cli-reference.md)
- [Agent 使用约束](skills/vmware-debug/references/agent-guardrails.md)
- [详细用法与架构](skills/vmware-debug/references/detailed-guide-CN.md)
- [唯一技能指令正本](skills/vmware-debug/SKILL.md)

属于 [VMware Skills 家族](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)：15 个 skill、14 个 MCP server 与一个共享 Policy 库。如果对你有用，欢迎 Star 家族门户。

[问题与反馈](https://github.com/vmware-skills/VMware-Debug/issues) · [MIT](LICENSE)
