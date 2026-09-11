<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware Debug（中文）

> **声明**：本项目为社区维护的开源项目，**与 VMware, Inc. 或 Broadcom Inc. 无任何隶属、
> 背书或赞助关系。** "VMware"、"vSphere" 为 Broadcom 商标。源码以 MIT 许可证公开可审计。

VMware skill 家族的**诊断大脑**。你给出症状（报错、日志、变慢的 VM），它来跑系统化排查：
把其它 skill 取到的事件关联成一条时间线、检测突刺、给根因假设排序，并告诉你下一步该查什么。
它**从不触碰 vSphere**——不连接任何东西，也从不执行修复；唯一的写入是 `$OPS_HOME` 下的本地案卷。修复一律路由给 vmware-aiops（单步）或
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

## MCP 工具（14 个：7 读、7 写）

7 个写工具只写本地案卷，没有一个触及任何 VMware 系统。

**关联**——无状态，看一眼用：

- `incident_timeline`：[READ] 把已取到的事件关联成 时间线 + 突刺 + 排序后的根因假设 + 下一步检查建议
- `list_symptom_categories`：[READ] 症状类别及对应的排查路由（不知道查什么时用它）

**调查案卷**——需要持续推理的事件：

- `case_open`：[WRITE] 定义事件；返回案卷 id 和本环境能达到的结论等级
- `case_readiness`：[READ] 开始**之前**，按症状类别说明本环境能达到的等级
- `case_knowledge`：[READ] 接受哪些知识格式、挂载了什么、哪些条目适用于某个案卷
- `case_plan`：[READ] 下一步该取什么——每步给出 skill、工具和目的；按案卷当前状态重算
- `case_list`：[READ] 案卷列表，最新的在前
- `case_get`：[READ] 单个案卷：范围、账本大小、等级历史
- `case_hypotheses`：[WRITE] 登记一个候选解释，或读取各假设的支持/反驳账本
- `case_submit_evidence`：[WRITE] 记录一条取到的事实，连同来源、查询和时间基准
- `case_record_gap`：[WRITE] 记录**没能**取到的东西，以及如何补上
- `case_timeline`：[WRITE] 把案卷收集到的一切关联成一条时间线
- `case_grade`：[WRITE] 从账本重算结论等级并记录
- `case_close`：[WRITE] 记录最终等级、归档，并写明留下了哪些未决项

**事件信封**：`{ts, source, severity, entity, text, fields}`。agent 把各源事件归一成此形状再交给
debug；debug 因此与其它包零运行时依赖。**请把每条事件的 `event_type` 保留在 `fields` 里**——
分类器除了正文之外也匹配它，而现代 vSphere 多数事件是 `EventEx`：正文是一句通用套话，
真正说明「这是什么事件」的只有 `eventTypeId`。

## 安全

结构上离线、无凭据：不连任何 vCenter/NSX/Aria，对 VMware 没有可破坏面，也没有秘密可泄露。
唯一的写入是本地案卷：证据、缺口、假设和等级历史只增不改；`timeline.md` 由证据重新生成，
`case.json` 保存当前等级和状态。前提是每个案卷只有一个写入者：缺口和假设是整读、追加、整写，没有加锁，
两个进程在共享的 `$OPS_HOME` 里同时写同一个案卷可能丢条目。
详见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT。
