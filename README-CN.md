<!-- mcp-name: io.github.zw008/vmware-debug -->

# VMware Debug（中文）

> **声明**：本项目为社区维护的开源项目，**与 VMware, Inc. 或 Broadcom Inc. 无任何隶属、
> 背书或赞助关系。** "VMware"、"vSphere" 为 Broadcom 商标。源码以 MIT 许可证公开可审计。

VMware skill 家族的**诊断大脑**。你给出症状（报错、日志、变慢的 VM），它来跑系统化排查：
把其它 skill 取到的事件关联成一条时间线、检测突刺、给根因假设排序，并告诉你下一步该查什么。
**只读**——从不修改任何东西，也从不执行修复。修复一律路由给 vmware-aiops（单步）或
vmware-pilot（多步、带审批门控），完全复刻 vmware-harden → vmware-pilot 的「顾问/执行」分工。

- **设计上只读，且可证明**（v1.8.0）—— 2 个 MCP 工具全为只读、零写工具；设置 `VMWARE_READ_ONLY=true`（或按 skill 的 `VMWARE_DEBUG_READ_ONLY`），家族只读闸门会在启动时验证这一点，而不是让你相信文档——本 skill 没有配置文件，环境变量是唯一开关，详见[只读模式](#只读模式)

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

## 只读模式

vmware-debug 结构上就是只读的——两个 MCP 工具都带 `[READ]` 标记，不接收任何凭据，也完全不发起
网络调用；它们只是对 agent 用其它 skill 的读工具**已经取到**的事件字典做关联分析。自 v1.8.0 起，
这一点从「文档承诺」变成了**可证明的事实**：设置 `VMWARE_READ_ONLY=true`，家族只读门控会在启动时
枚举注册表并验证暴露的写工具数为零——这是结构性保证，而非模型可以无视的提示词约束。**默认关闭**；
且为 fail-closed 设计：请求了只读模式但无法保证时，服务器直接拒绝启动，而不是敞开运行。

同一个变量是家族级的：一个环境变量同时会把有写能力的兄弟 skill（aiops、storage、vks、nsx 等）的
全部写工具剥离，因此「整个环境切只读」只需一处设置。

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

- **按 skill 覆盖**：`VMWARE_DEBUG_READ_ONLY` 优先于家族级 `VMWARE_READ_ONLY`。vmware-debug
  没有 `config.yaml`，因此环境变量是唯一的开关。优先级：按 skill 环境变量 → 家族环境变量 → 默认关闭。
- **分类依据**：本 skill 通过 `build_server()` 工厂注册工具，不传 annotations，因此门控依据
  `[READ]`/`[WRITE]` docstring 标记分类。凡是无法证明为只读的，一律按写工具处理。
- **启动日志**：不会打印任何「被移除」的工具，因为确实一个都没有——门控返回空结果本身就是这个断言
  （有写能力的兄弟 skill 则会打印 `Read-only mode active ... withheld N write tool(s)`）。

## 安全

结构上只读、离线、无凭据：不连任何 vCenter/NSX/Aria，没有可破坏面，也没有秘密可泄露。
详见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT。
