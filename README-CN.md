<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware Debug（中文）

> **声明**：本项目为社区维护的开源项目，**与 VMware, Inc. 或 Broadcom Inc. 无任何隶属、
> 背书或赞助关系。** "VMware"、"vSphere" 为 Broadcom 商标。源码以 MIT 许可证公开可审计。

VMware skill 家族的**诊断大脑**。你给出症状（报错、日志、变慢的 VM），它来跑系统化排查：
把其它 skill 取到的事件关联成一条时间线、检测突刺、给根因假设排序，并告诉你下一步该查什么。
**只读**——从不修改任何东西，也从不执行修复。修复一律路由给 vmware-aiops（单步）或
vmware-pilot（多步、带审批门控），完全复刻 vmware-harden → vmware-pilot 的「顾问/执行」分工。

## 配套 Skill

| 需求 | Skill |
|---|---|
| 故障关联 / 根因 | **vmware-debug**（本项目） |
| 集中日志检索 | vmware-log-insight（把 `log_search` 结果喂给它） |
| vCenter 事件与告警 | vmware-monitor |
| 指标 / 异常 | vmware-aria |
| 执行修复 | vmware-aiops（单步）/ vmware-pilot（多步门控） |

## 安装

```bash
uv tool install vmware-debug
vmware-debug categories          # 看它能诊断哪些症状类别
```

## MCP 工具（2 个，全只读）

- `incident_timeline`：把已取到的事件关联成 时间线 + 突刺 + 排序后的根因假设 + 下一步检查建议
- `list_symptom_categories`：症状类别及对应的排查路由（不知道查什么时用它）

**事件信封**：`{ts, source, severity, entity, text, fields}`。agent 把各源事件归一成此形状再交给
debug；debug 因此与其它包零运行时依赖。

## 安全

结构上只读、离线、无凭据：不连任何 vCenter/NSX/Aria，没有可破坏面，也没有秘密可泄露。
详见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT。
