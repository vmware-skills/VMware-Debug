# vmware-debug 调查层设计（MetaRAG 方法论落地）

> 状态：**设计中**，已验证第 1–2 段，第 3 段起未定。代码未动。
> 来源：《AI 辅助日志分析 · 鹏鼎 VMware 技术交流日》（2026-08）第 4 / 6 / 11 / 12 / 13 页。

## 一句话

把六级价值阶梯里家族最空的三级——**判、行、学**——做成 `vmware-debug` 的能力：
它是调查的**账本和裁判**，不是取证的手。

## 为什么落在 debug，而不是新建 skill

八步证据闭环里有六步是**跨 skill 的状态**（事件范围、Evidence ID、假设账本、
时间线、知识约束、结论等级）。塞进任何一个数据源 skill 都会变形；新建第 16 个
skill 又会把家族的入口再切一刀。debug 已经被定义为「诊断大脑」，且今天只有
2 个工具、914 行——它的问题不是位置不对，是**没有状态**。

## 已定的三个决定

### D1 载体：vmware-debug，不新建 skill
产品形态按 **B 起步、留 C 的接口**：B = 调度现有 skill 的调查层；
C = 面向客户的引导壳（"你的日志在哪、设备怎么连、知识库挂什么"），
作为 debug 之上的一层引导，不做成独立产品形态。

### D2 状态：case 目录为主，SQLite 只做索引
```
~/.vmware/cases/<case-id>/        # OPS_HOME 可重定向到共享盘 / 工单系统挂载点
├── scope.json        对象·版本·时间窗·范围，以及它是怎么被确定的
├── plan.jsonl        每条取证指令：skill·工具·参数·目的·状态
├── evidence/         每条证据一个 Evidence ID：来源·原始查询·取回时间
├── gaps.json         该取没取到的：原因·影响哪个假设·怎么补
├── timeline.md       Trigger · Symptom · Propagation · Recovery
├── hypotheses.md     假设账本：支持证据 · 反证 · 缺口 · 下一步
├── conclusion.md     分级结论 + 升级依据 + 未验证项
└── case.json         索引摘要（同步写入 investigation.db）
```
除 `case.json` 外**全部人可读文本**。这个目录本身就是交付物（PPT 第 3 页的
"证据包"）：客户整包拿走、自己翻、自己审结论怎么来的；也能在**没有任何 VMware
环境**的机器上用 debug 重新审阅——这是 D3 保住零凭据换来的能力。

索引与目录将来可统一，先不合并。

### D3 证据流：Agent 编排，debug 永不持凭据
debug 输出**结构化取证指令**，模型去调 Monitor / Aria / Log-Insight / NSX 等，
把结果交回 debug 入库校验。debug 对 VMware 环境保持零连接、零凭据。

前提条件——**证据契约要硬**：
- `case_plan` 输出可执行结构（skill·tool·params·purpose·blocks_hypothesis），
  不是散文提示。散文会被模型自由发挥，结构化指令不会。
- `case_submit_evidence` 收证时做第 03 步校验：时间戳归一、时钟偏差检测、实体对齐；
  **取不到的显式记 gap，绝不当作"没问题"**。

最后这条是 harden v1.10.0「采集器跑了 ≠ 这台主机被判定了」的同构——
合规域已经把这个纪律写对过一遍，调查域是复用，不是新发明。
参见家族 CLAUDE.md 反复出现的错误形态 #1：空结果读作「没问题」。

## 调查状态机（对应 PPT 第 6 页八步）

```
open       → 01 定义事件            → scope.json
collecting → 02/03 取证 + 规范校验   → evidence/ + gaps.json
analyzing  → 04/05 压缩排序 + 时间线 → timeline.md
hypothesis → 06 假设账本            → hypotheses.md
grading    → 07/08 知识校验 + 分级   → conclusion.md
closed     → 归档 + 索引 + 沉淀
```

## 工具面（9 新 + 2 保留 = 11）

| 工具 | 读写 | 作用 |
|---|:--:|---|
| `case_open` | W | 定义事件 → case_id + 第一批取证清单 |
| `case_plan` | R | 下一步该取什么（随假设状态变化，非静态清单）|
| `case_submit_evidence` | W | 收证 + 规范校验 + 落 gap |
| `case_timeline` | R | 压缩排序 + Trigger/Symptom/Propagation/Recovery |
| `case_hypotheses` | R | 假设账本当前状态 |
| `case_grade` | W | 结论分级，**升级规则不满足即拒绝升级** |
| `case_close` | W | 归档 + 写索引 + 沉淀为案例 |
| `case_list` / `case_get` | R | 跨案例检索，支撑「学」与 Eval |
| `incident_timeline` | R | 保留：无 case 的一次性用法 |
| `list_symptom_categories` | R | 保留 |

发版前必须过 `tests/eval/capability/test_tool_manifest_budget.py`。

### 证据源目录（第 11 页「日志源智能路由」）
什么对象 × 什么故障类型 → 该调哪个 skill 的哪个工具。
**必须是数据文件，不能写死在 prompt 里**，并接上 `family_smoke` 既有的
「文档里每个工具名都必须真实存在于家族 MCP registry」检查，杜绝幽灵工具。

### READ-ONLY 口径修正
现在 SKILL.md 写「READ-ONLY: it never changes anything」。加了 case 之后：
- 对 **VMware 环境**仍是零写入——这个承诺不变，是 debug 的卖点
- 对**本地 case 状态**是写：四个 W 工具照家族规矩挂 `@vmware_tool` 审计

措辞要改准，不能含糊过去。**但要等工具真的存在之后再改**——
今天 debug 确实什么都不写，提前改反而是另一个方向的假话。

## 未定（待续的设计段落）

- **第 3 段 结论治理**：Candidate / Probable / Confirmed / Excluded 的**升级规则**。
  PPT 第 13 页的硬约束："没有直接硬件诊断时停留在 Candidate 或 Probable"。
  规则写在哪、谁执行、怎么防止模型自行升级。
- **第 4 段 知识层**：KB / Runbook / 历史案例怎么挂载；第 07 步「版本约束」
  （产品·Build·驱动·固件的适用性）怎么做成硬过滤而不是相似度匹配。
- **第 5 段 C 层引导**：首次接入问客户什么、readiness 怎么判、缺什么怎么提示。
- **第 6 段 对其余 14 个 skill 的要求**：证据契约的落地面（哪些工具要补
  Evidence 元数据：来源、查询、时间基准）。
- **第 7 段 测试与 Eval**：PPT 第 16 页四类指标——首次有效证据耗时、关键证据
  召回率、**错误 Confirmed 率**、下一步可执行率——怎么变成 CI 里跑得动的东西。

## 已知的物理层缺口（与 MHS 无关，但同一块拼图）

PPT 第 9/10/13 页反复指向 ESXi 以下：BMC、Core Dump、SMART/NVMe、驱动固件、
物理交换机。第 13 页那个 vSAN 案例卡在 Probable，原因写得很直白：
**"缺口：SMART/NVMe 直接诊断"**。家族今天对这一层完全是盲的。

补一个 Redfish/BMC + SMART/NVMe 证据源，是让「Probable → Confirmed」走通的
前提。用普通 MCP 就够，不必等 Anthropic 的 Model Hardware Standard（2026-08-27
research preview，目标是实验室仪器与先进制造，不是数据中心基础设施）。
接口按 read 原语设计，将来若 MHS 开源可平移。
