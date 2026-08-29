# vmware-debug 调查层设计（MetaRAG 方法论落地）

> 状态：**七段定稿；§3 已落地为代码**（`vmware_debug/ops/cases/` + `rules/grading_rules.yaml`，
> 81 个测试）。§4–§7 仍是设计。
> 未定的只剩实现顺序，以及两处**外部依赖**：知识库内容为空、硬件层无通道——
> 这两项决定了今天的结论上限是 Probable，见第 4 段与文末。
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

## 工具面（10 新 + 2 保留 = 12）

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
| `case_readiness` | R | 第 5 段：这个环境的结论上限在哪，缺什么、怎么补 |
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

## 第 3 段 结论治理：等级是算出来的，不是模型报的

四级沿用 PPT 第 12/13 页：**Candidate / Probable / Confirmed / Excluded**。

### 唯一的机制决定：`case_grade` 不接受 grade 参数

模型**没有**提出等级的入口。它只能提交证据，等级由账本算出来。
这是 harden v1.9.0「规则跑了 ≠ 那台主机被判定了」的同构——那次的教训是
**留一个让程序自己宣布结论的口子，它就会用**。这里直接把口子焊死。

`case_grade` 每次调用都从 `hypotheses.md` + `evidence/` + `gaps.json`
**重算**，不读上一次的存档。等级因此是账本的函数，不是一个存下来就
不再被质疑的决定。

### 升级规则：内置默认 + 规则文件覆盖

```
vmware_debug/rules/grading_rules.yaml      内置默认，随包发布
~/.vmware/investigation/grading_rules.yaml 站点覆盖（可选）
```

覆盖是**按等级块整体替换**，不做深合并——深合并出来的最终规则没人能
一眼读懂，而这是一份要被客户审的文件。`case_grade` 的输出必须写明
本次用的是哪个文件的哪个块。

### 内置默认（首版）

| 等级 | 条件 |
|---|---|
| **Candidate** | 默认。有假设即可 |
| **Probable** | ≥2 个**独立来源**的证据互相印证，且没有「一旦补上就可能推翻它」的未决 gap |
| **Confirmed** | 上述 + 至少一条**决定性证据** + **任何一类阻塞 gap 都不能开着** |
| **Excluded** | 必须有**明确的证伪观测**——「没找到」是 gap，不是排除 |

**决定性证据**三类（第 3 类起是用户本轮加的）：

1. **直接硬件/设备诊断** —— SMART/NVMe、BMC/Redfish、core dump、驱动固件读数
2. **KB 库条目**，且其适用性约束经过校验（见第 4 段）
3. **厂商 SR / 官方案例**，且结论明确指向同一机制

### gap 分两类，这个区分是承重的（实现时才发现）

写代码走 PPT 第 13 页那个 vSAN 案例时暴露的：第一版把「任何阻塞 gap」都拿来卡
Probable，于是那个案例被判成 Candidate——**而它的作者自己判的是 Probable**。

区别在于「这条 gap 补上之后，可能推翻假设吗」：

| | 例子 | 效果 |
|---|---|---|
| **缺佐证** `could_falsify: false` | 拿不到 SMART 读数（拿到多半是**印证**它坏了）| **封顶**在 Probable，不降级 |
| **可能推翻** `could_falsify: true` | 08:30 有没有推过固件（有的话根因换人）| **压在** Candidate |

上面那张表里 Probable 那行的原话「没有**一旦补上就可能推翻它**的未决 gap」本来就是
这个意思，是实现把它塌缩成了「任何 gap」。

**这不只是判得准不准的问题，它决定这套账本能不能用。** 如果记一条 gap 要付两级代价，
最划算的做法就是不记——而 gap 账本是这里唯一**绝不能有更便宜替代品**的东西。
诚实必须免费。

**Excluded 那一行是这份文档里最容易被绕过的一行。** 「查了没发现」和
「查到了证明不是它」在自然语言里几乎同形，但前者是 gap 后者才是排除——
这正是家族反复出现的错误形态 #1（空结果读作「没问题」）在结论域的形状。
`case_grade` 判 Excluded 时必须能指出**那条证伪观测的 Evidence ID**，指不出就不判。

### 允许降级

新证据与已有结论矛盾，或一个曾被当作已关闭的 gap 重新打开时，等级**下降**。
`conclusion.md` 保留完整的等级变更史（时间、前后等级、触发的那条证据 ID、
用的哪条规则），**永不静默改写**。

只能升不能降的系统，会把「我们后来发现搞错了」这件事挤到文档之外——
而调查交付物的价值，恰恰在于它敢记录自己曾经错过。

### 一条硬约束（PPT 第 13 页原话）

> 没有直接硬件诊断时，停留在 Candidate 或 Probable。

这条不是规则文件里的一行，是**默认规则的形状本身**：Confirmed 需要决定性证据，
而家族今天对硬件层完全是盲的（见文末物理层缺口）。所以在补上 Redfish/SMART
证据源之前，硬件类根因**在机制上**升不到 Confirmed，不靠自觉。

---

## 第 4 段 知识层：硬过滤，不是相似度

### 挂载点

```
~/.vmware/knowledge/
├── kb/       产品知识库条目
├── runbook/  处置手册
├── sr/       厂商 SR / 官方案例
└── cases/    本地历史案例（case_close 沉淀的产物）
```

### 可接受的文件格式

**开箱即读**（无新依赖）：

| 格式 | 后缀 | 说明 |
|---|---|---|
| Markdown | `.md` `.markdown` | 首选。正文自由，元数据放 YAML front-matter |
| 纯文本 | `.txt` `.log` | 正文按整篇处理，元数据须另配同名 `.yaml` |
| YAML | `.yaml` `.yml` | 结构化条目，适合规则型 KB |
| JSON / JSONL | `.json` `.jsonl` | 批量导出（工单系统导出通常是这个） |
| CSV / TSV | `.csv` `.tsv` | 表格型 KB 索引，一行一条 |

**需要先转换**（不进包，给转换指引即可）：PDF、DOCX、PPTX、HTML、
Confluence/Notion 导出。转成 Markdown 后再放进来——把解析器塞进 debug
会让它长出一堆与调查无关的依赖面。

### 每条知识必须带适用性块，否则不具备决定性

```yaml
---
id: KB-2026-0417
applies_to:
  product: vsphere
  build: ">=8.0.3, <9.0"        # 缺省 = 不限，但会降级为「仅支撑」
  driver: {name: nvme_pcie, version: ">=1.2.4"}
  firmware: {vendor: dell, model: PERC-H755, version: ">=52.26"}
  hardware_model: ["PowerEdge R750"]
mechanism: "..."                 # 它主张的机制
falsifies: []                    # 它能排除掉什么
---
```

**这是第 4 段的全部要点：命中不是靠相似度，是靠 `applies_to` 对着
`scope.json` 逐项过。** PPT 第 07 步叫「版本约束校验」——一条 KB 描述得
再像，产品线/Build/驱动/固件对不上就是不适用。相似度匹配在这里会**制造**
错误的 Confirmed，而错误 Confirmed 是第 7 段里唯一一个必须压到零的指标。

`applies_to` 缺失或校验不通过的条目，仍可作为**支撑证据**参与 Probable，
但**永远不能**充当决定性证据。

### ⚠️ 这一层现在是空的，用户需要知道

上面定义的是**容器**，不是内容。`~/.vmware/knowledge/` 今天没有任何条目，
家族也不附带任何 KB。直接后果：

- **KB 路径和 SR 路径都无法把结论推到 Confirmed**——不是"效果差"，是机制上走不通
- 加上硬件层同样缺失（见文末），**今天这套调查层的结论上限是 Probable**

`case_grade` 必须把这句话打在输出里，而不是让用户从"怎么老是 Probable"
里自己猜。内容按需叠加：客户有自己的 KB 就挂客户的，有 SR 权限就导出 SR。

---

## 第 5 段 C 层引导：先告诉客户能得出什么结论，再开始调查

C 层不是新产品形态，是 debug 上的一层引导壳。它的第一个能力是
`case_readiness`——**在调查开始前**回答一个问题：

> 以你现在的环境，结论最高能到哪一级？

### 首次接入问什么

按**证据类**问，不按 skill 问（客户不知道 skill 是什么）：

| 证据类 | 问法 | 对应家族能力 |
|---|---|---|
| 虚拟化状态 | vCenter 能连吗？只读账号有吗？ | Monitor / AIops |
| 日志 | 集中日志在哪？Log Insight / syslog / 只有本地？ | Log-Insight |
| 指标与告警 | 有 Aria / VCF Ops 吗？留存多久？ | Aria |
| 存储 | vSAN 还是外部阵列？阵列侧能取到诊断吗？ | Storage |
| 网络 | NSX 有吗？物理交换机侧谁管？ | NSX / NSX-Security |
| **硬件** | **BMC/iDRAC/iLO 能访问吗？SMART 能读吗？** | **无 —— 家族缺口** |
| **知识** | **有内部 KB 吗？厂商 SR 能查吗？** | **无 —— 空目录** |
| 时间基准 | 各系统 NTP 同源吗？ | 决定时间线可信度 |

### readiness 分类目，不给总分

一个笼统的"就绪度 78%"没有任何可操作性。输出应当是：

```
存储类故障   → 可达 Probable（缺 SMART/NVMe 直接诊断，无法 Confirmed）
网络类故障   → 可达 Probable
配置漂移     → 可达 Confirmed（harden 提供逐节点判定）
硬件类故障   → 仅 Candidate（无 BMC 通道，无 KB）
```

**这是本设计里唯一"提前认输"的地方，也是它最该有的诚实。** 调查做到一半
才说"这个我们查不了"，和开始前就说清楚，价值差一个数量级。

### 缺什么怎么提示

每条不可达都必须给出**补法**，而不只是报缺：缺 Log Insight → 可退化为
逐主机 `host_log_scan`（Monitor 有，慢但能跑）；缺 BMC → 指出这是外部
动作，需要谁配合；缺 KB → 说明可接受的格式（第 4 段那张表）。

---

## 第 6 段 对其余 14 个 skill 的证据契约要求

### 先说一个已经存在的好消息

`~/.vmware/audit.db` 的 `audit_log` 表现有字段：

```
ts · skill · tool · params · result · status · duration_ms · workflow_id · ...
```

**Evidence 记录需要的五项里，四项已经在了**——来源 skill、具体工具、
原始查询参数、取回时间。而 `workflow_id` 天然就是 case 关联键。
也就是说 Tier 1 的证据溯源**不需要改任何一个 skill**：debug 用 case_id
作为 workflow_id，事后从审计库反查这条证据是谁、用什么参数、什么时候取的。

### 缺的是第五项：数据自身的时间基准

「什么时候取的」≠「数据覆盖哪段时间」。一次 `list_events(hours=24)` 在
10:00 取和在 18:00 取，覆盖窗口完全不同；而时间线关联（第 05 步）
靠的正是后者。这一项审计库里没有，**必须由工具自己报**。

### 两级要求，不要求 14 个仓一起动

**Tier 1（零改动，立即可用）**：debug 从 `audit_log` 反查溯源。适用于全部 15 仓。

**Tier 2（按仓改）**：**带时间窗的读工具**在返回里加 `evidence` 块。
`vmware_policy.envelope.paginated()` 的 `**extra` 已经允许追加顶层键，
所以这是加参数、不是改签名：

```python
return paginated(rows, limit=limit, target=target, evidence={
    "window_start": ..., "window_end": ...,   # 数据覆盖的窗口（不是取回时刻）
    "time_source":  "vcenter" | "host" | "client",
    "clock_skew_s": ...,                       # 已知偏差，未知则 None
})
```

`time_source` 是第 03 步时钟偏差检测的输入。**未知就显式写 None，不要省略键**——
省略的键正是模型会拿想象去填的地方（envelope.py 的文档注释里已经写过这条纪律）。

### 落地顺序按「谁真的回答调查问题」排

先 **Monitor · Log-Insight · Aria** 三个（时间窗证据几乎全在这里），
再 **Storage · NSX**。AVI / VDI / PrivateAI / Pilot 暂不要求。

### 一个必须先解决的前置

实测家族 `paginated()` 调用分布极不均匀：

```
Monitor 27 · Aria 19 · NSX 10 · VKS 8 · Harden 7 · AIops 6 · NSX-Sec 6 · Storage 5 · Log-Insight 3
AVI 0 · VDI 0 · PrivateAI 0 · Pilot 0
```

四个仓一次都没用。**证据契约不能建在一个 1/4 的仓还没采纳的载体上**——
Tier 2 的范围要么限定在已采纳的仓，要么先补齐 envelope。前者是这版的选择。

---

## 第 7 段 Eval：把「错误 Confirmed 率」变成能在 CI 里跑红的东西

PPT 第 16 页四个指标，逐个落到可执行形态：

| 指标 | 怎么变成 CI 能跑的 | 目录 |
|---|---|---|
| **错误 Confirmed 率** | fixture case，正确答案是**「不得达到 Confirmed」**。grade 是账本的纯函数，不需要真环境 | `regression/` |
| **关键证据召回率** | fixture 声明必需证据集；断言 `case_plan` 最终把它们**全部**要过 | `capability/` |
| **首次有效证据耗时** | 不测墙钟。测**第一批 plan 里是否含有那个真正藏着答案的工具** | `capability/` |
| **下一步可执行率** | 每条 plan 项在结构上可执行：skill·tool 真实存在于家族 MCP registry，params 通过 schema | `regression/` |

第四项**直接复用** `family_smoke` 已有的「文档里的工具名必须真实存在」
检查（那道闸就是为了杜绝幽灵工具建的），不是新造轮子。

### 三条纪律，写在 eval 之前

1. **每个 eval 必须做变异测试**——故意注入缺陷，确认它真的变红。
   本家族出过「永远变绿的检查」，一个不再检查任何东西的检查比没有检查更危险。
2. **错误 Confirmed 的 fixture 要覆盖"看起来很像"的场景**，尤其是
   KB 相似但 `applies_to` 对不上的那一类——这正是第 4 段硬过滤要挡的东西。
   只测明显不该 Confirmed 的例子，等于形态 #2（检查验的是已经知道答案的事）。
3. **fixture 里必须有一例的正确答案是 Excluded**，且必须是靠证伪观测排除的。
   没有这一例，Excluded 那条规则会在无人注意时退化成「查了没发现」。


## 已知的物理层缺口（与 MHS 无关，但同一块拼图）

PPT 第 9/10/13 页反复指向 ESXi 以下：BMC、Core Dump、SMART/NVMe、驱动固件、
物理交换机。第 13 页那个 vSAN 案例卡在 Probable，原因写得很直白：
**"缺口：SMART/NVMe 直接诊断"**。家族今天对这一层完全是盲的。

补一个 Redfish/BMC + SMART/NVMe 证据源，是让「Probable → Confirmed」走通的
前提。用普通 MCP 就够，不必等 Anthropic 的 Model Hardware Standard（2026-08-27
research preview，目标是实验室仪器与先进制造，不是数据中心基础设施）。
接口按 read 原语设计，将来若 MHS 开源可平移。
