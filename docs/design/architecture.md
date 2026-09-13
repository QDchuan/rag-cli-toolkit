# 数据预处理阶段 — 架构设计

> 本文档回答三个问题：架构怎么设计、Agent 怎么设计、还缺什么。
> 读者是项目决策者，不是 Agent——Agent 手册在 `parse-worker.md` / `chunk-worker.md` / `tagger-worker.md` / `summarize-worker.md`。

---

## 一、先厘清一个关键区分

现有资产里混着**两种性质完全不同的"专家"**，它们的 Agent 设计必须不同。这是整个架构的地基。

| | Type A：工具操作型 | Type B：判断型 |
|---|---|---|
| **代表** | `parse` | `chunk` / `summarize` / `tagger` |
| **难点在哪** | 已由开源引擎解决（pdfplumber、python-docx…） | 在**决策**：切多细？要不要摘要？Schema 怎么定？ |
| **Agent 做什么** | 路由 + 传参 + 校验 | 分析 + 决策 + 必要时写代码 |
| **手册性质** | **操作手册**（怎么调、参数含义、报错怎么办） | **决策框架**（判断依据、取舍原则、陷阱） |
| **失败模式** | 选错引擎、依赖缺失、参数错 | 粒度错、语义完整性被破坏、Schema 设计失败 |
| **模型档位** | 小模型够用（便宜、快） | 需要强模型（判断力） |
| **确定性** | 幂等：同输入同输出 | 非幂等：可能每次决策不同 |

**为什么这个区分重要：**

`parse-worker.md` 现在写得像决策框架，其实它应该是操作手册——因为真正的脏活（PDF 多栏、合并单元格、OCR 版面）都被 `ragcli parse` 封装了，Agent 不需要"判断"，只需要"正确调用"。

而 `chunk-worker.md` 反过来——它必须是决策框架，因为没有工具能替 Agent 决定"这份 API 文档该不该切"。你也明确说过：**切块专家得自己写脚本**。

→ **结论：Type A 的 Agent 可以做得很薄（甚至可以退化成纯代码，不需要 LLM）；Type B 的 Agent 必须是完整的、有独立上下文的推理单元。**

---

## 二、阶段边界：写路径 vs 读路径

之前把 `embed`/`index` 划成独立的 "index" 阶段，现在看这个切分不够干净。更准确的边界是：

```
┌─────────────── 数据预处理 Agent（写路径）───────────────┐
│                                                          │
│  parse → clean → chunk → enrich → embed → index          │
│           ↑        ↑       ↑                          │
│        已封装    Type B   Type B                       │
│                            (summarize ∥ tagger)        │
└──────────────────────────────────────────────────────────┘
                            ↓  交付：可检索的向量库 + 元数据
┌─────────────── 检索 Agent（读路径）─────────────────────┐
│  query → search → rerank → generate                     │
└──────────────────────────────────────────────────────────┘
```

**理由**：`embed`/`index` 是**写操作**，和 `parse` 一样只发生在入库时；`search`/`rerank` 是**读操作**，发生在每次查询。按读写分，比按"是不是构建索引"分更符合真实职责。

→ 建议把 registry 的 `index` 阶段合并进 `ingest`，只保留三个阶段：`ingest` / `retrieve` / `evaluate`。

---

## 三、Agent 拓扑

```
                    ┌─────────────────────────┐
                    │   Ingest Orchestrator   │
                    │  持有：orchestration-    │
                    │  guide + 契约定义        │
                    │  ❌ 不读任何 expert MD   │
                    └───────────┬─────────────┘
                                │ 按 DAG 调度
        ┌───────────┬───────────┼───────────┬────────────┐
        ▼           ▼           ▼           ▼            ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ Parse   │ │ Chunk   │ │Summarize│ │ Tagger  │ │ Index   │
   │ Worker  │ │ Worker  │ │ Worker  │ │ Worker  │ │ Worker  │
   ├─────────┤ ├─────────┤ ├─────────┤ ├─────────┤ ├─────────┤
   │Type A   │ │Type B   │ │Type B   │ │Type B   │ │Type A   │
   │小模型    │ │强模型    │ │强模型    │ │强模型    │ │无 LLM   │
   │parse-   │ │chunk-   │ │summarize│ │tagger-  │ │纯代码    │
   │expert   │ │expert   │ │-expert  │ │expert   │ │          │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
        │           │           │           │            │
        └───────────┴───────────┴───────────┴────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   Artifact Store         │
                    │  （文件系统 / 对象存储）  │
                    │  每个阶段落盘 + manifest  │
                    └─────────────────────────┘
```

### 三条设计原则

**1. 上下文隔离靠"进程隔离"，不靠"提示词说别读"**
每个 Worker 是**独立 session**，只注入自己的 MD。Orchestrator 永远不读 expert MD——它只需要知道"有个叫 Chunk Worker 的东西，给它 parsed.json 它返回 chunks.json"。这正是你之前担心的污染问题的根本解法。

**2. 阶段间靠 Artifact 通信，不靠对话**
Worker 之间零对话。所有交接通过文件系统上的 JSON 契约。好处：可重放、可单测、可人工插入检查。

**3. 每个 Worker 无状态**
Worker 不记"上次处理到哪"，只接受输入产出输出。状态全部落在 manifest 里，由 Orchestrator 管理。这样才能重试和并行。

### 每个 Worker 的规格

| Worker | 模型档位 | 幂等 | 重试 | 可并行 |
|---|---|---|---|---|
| **Parse** | 小（Haiku 级） | ✅ 是 | ❌ 否（确定性，重试无意义） | ✅ 按文件并行 |
| **Chunk** | 强（Sonnet/Opus 级） | ❌ 否 | ✅ 是（校验失败带反馈重试 ≤2 次） | ✅ 按文档并行 |
| **Summarize** | 中（判断少，主要执行） | ❌ 否 | ✅ 是（限流退避） | ✅ 按文档并行 |
| **Tagger** | 中（应用既定 Schema） | ❌ 否 | ✅ 是 | ✅ 按文档并行 |
| **Index** | 无 LLM | ✅ 是 | ✅ 是（网络重试） | ❌ 需串行（写同一集合） |

**注意 Chunk 非幂等这件事**：Agent 每次决策可能不同，这会导致同一文档两次入库产生不同的 chunks。**必须把 Agent 的决策（选了哪个策略、什么参数）记进 artifact**，否则无法复现、无法排查"为什么上周检索好好的"。

---

## 四、执行模型：DAG + Manifest

### 依赖图（`pipeline-dependencies.md` 已分析，这里落成可执行形式）

```
parse ──→ clean ──→ chunk ──┬──→ summarize ──┐
                            │                ├──→ embed ──→ index
                            └──→ tagger ─────┘
                                 ↑
                          （两者并行，都只依赖 chunk）
```

**关键：`embed` 必须等 `tagger` 和 `summarize` 都完成**——因为标签和摘要是要写进向量 payload 的，先 embed 再打标就得二次写入，浪费。

### Manifest：状态机

缺了这个，管线崩在 tagging 阶段就只能从头再来。建议结构：

```jsonc
{
  "manifest_version": 1,
  "document": {
    "source_path": "/raw/report.pdf",
    "source_hash": "sha256:abc...",     // 用于增量更新判断
    "source_id": "report_a1b2c3d4",
    "ingested_at": "2026-09-13T12:00:00Z"
  },
  "stages": {
    "parse":     {"status": "done",    "artifact": "artifacts/report/parsed.json",     "finished_at": "...", "attempts": 1},
    "clean":     {"status": "done",    "artifact": "artifacts/report/cleaned.json",    "finished_at": "...", "attempts": 1},
    "chunk":     {"status": "done",    "artifact": "artifacts/report/chunks.json",     "finished_at": "...", "attempts": 2,
                  "decision": {"strategy": "by_header", "chunk_size": 512, "overlap": 64}},
    "summarize": {"status": "done",    "artifact": "artifacts/report/summarized.json", "finished_at": "...", "attempts": 1},
    "tagger":    {"status": "running", "started_at": "...", "attempts": 1},
    "embed":     {"status": "pending"},
    "index":     {"status": "pending"}
  },
  "validation": {
    "parse": {"passed": true, "checks": [...]},
    "chunk": {"passed": true, "checks": [...]}
  },
  "cost": {"tokens_in": 12400, "tokens_out": 3200, "usd_estimate": 0.09}
}
```

**Manifest 带来的能力**：断点续跑、失败定位、成本归因、增量更新判断、审计（谁在什么时候用什么策略处理了这份文档）。

---

## 五、缺失清单

按"不补就出问题"的紧急度排序。**状态列标注本次重构的进展。**

### ✅ 本次重构已解决

| # | 项 | 怎么解决的 |
|---|---|---|
| R1 | **一份 MD 覆盖全流程的反模式** | 删除 `data-preprocessing-expert.md`（它引用了根本不存在的 `clean` 工具，且与四份专家手册口径不一） |
| R2 | **文档给谁看不明确** | `docs/` 拆成 `agents/`（Agent 手册，英文）/ `design/`（设计文档，中文）/ `reference/`（自动生成），并加 `docs/README.md` 文档地图 |
| R3 | **编排手册与实际拓扑不符** | `orchestrator.md` 重写：4 个 worker（parse/chunk/summarize/tagger）+ 2 个 tool step（embed/index），并明确"永不读 worker 手册" |
| R4 | **Tag Schema 语料级 vs 文档级的边界** | `tagger-worker.md` 加 "What You Receive" 节：Agent **应用**语料级 Schema，**提议**变更但不自行发明值 |
| R5 | **Chunk 策略 vs 粒度的边界** | `chunk-worker.md` 加 "What You Receive" 节：语料策略定 token 区间/overlap/保护结构，Agent 只定切分策略与边界 |
| R6 | **Chunk 工具与"专家自己写脚本"的矛盾** | `chunk-worker.md` 明确：三个原语可用，**`ragcli chunk` 只是简单文档的兜底**，专家应自己写脚本 |
| R7 | **阶段划分按"是不是索引"切不干净** | registry 从 4 阶段（含 `index`）改为 3 阶段：`ingest`（写路径）/ `retrieve`（读路径）/ `evaluate` |
| R8 | **文档引用了不存在的 `clean` 命令** | `pipeline-dependencies.md` 重写，并说明清洗在 `parse` 内部 |
| R9 | **CLI 文档与实现漂移** | `reference/cli.md` 改为 `tests/gen_cli_reference.py` 自动生成，禁止手改 |

### 🔴 P0 — 仍然缺，不补跑不起来

| # | 缺什么 | 为什么致命 | 建议 |
|---|---|---|---|
| 1 | **正式的 Artifact 契约（JSON Schema）** | 各手册现在用散文描述输出格式；Chunk 输出加字段、Tagger 没预期 → **静默错位**。语义已经在手册里写明，但**没有可执行的校验** | 建 `contracts/` 目录，每个 artifact 一份 JSON Schema + 版本号；加 `ragcli validate --stage chunk` |
| 2 | **Manifest / 状态机** | 崩了就重来，无法增量，无法审计 | 结构已在 `orchestrator.md` 定义，但**没有实现**；做成 `ragcli manifest init/show/update` |
| 3 | **语料级 Schema / 策略的存放（工具侧）** | 手册已要求 Agent"应用而非发明"，但**没有地方存放 Schema 和策略文件**，也没有 `ragcli schema` | 建 `corpus/schema.json` + `corpus/chunk-policy.json` + 校验命令 |

### 🟡 P1 — 生产环境必需

| # | 缺什么 | 问题 | 建议 |
|---|---|---|---|
| 4 | **阶段间质量门（工具侧）** | 手册列了断言，但没有**执行者** | 每阶段断言做成 `ragcli validate --stage X`，失败打回上一阶段 |
| 5 | **语料级去重** | `chunk-worker.md` 讲了近重复检测（按 heading path scope），但没有东西负责执行 | 加 corpus-level dedup 步骤，独立于单文档管线 |
| 6 | **增量更新** | 文档改了 → 全量重跑还是只更新变化部分？ | 用 `source_hash` 判断；文档级变更 → 整篇重跑；语料级 Schema 变更 → 只重跑 tagger |
| 7 | **Embedding 模型一致性约束** | embed 和 search 必须同模型；chunk 大小要适配模型 token 上限。跨 Agent 无人保证 | 建模型注册表，embed 时把模型名+维度写进 artifact，index/search 校验一致 |

### 🟢 P2 — 有了更好

| # | 缺什么 | 说明 |
|---|---|---|
| 8 | **成本预算与熔断** | 现在只能事后看账单。manifest 已定义 `cost` 字段，但没有执行逻辑 |
| 9 | **人工审核检查点** | Schema 变更、低置信度标签、大批量首次入库，值得插一道人工确认 |
| 10 | **可观测性面板** | 各阶段耗时、失败率、重试率、成本趋势 |

---

## 六、语料级 vs 文档级：已落进手册的边界

这两个边界在本次重构中已写进对应手册，列在这里是因为它们是**最容易再次搞混的地方**。

### 决策 1：Tag Schema 是语料级

```
语料级（定义一次，稳定）        文档级（Agent 每篇判断）
├── domain 枚举               ├── 这份文档属于哪个 domain
├── doc_type 枚举             ├── 它是什么体裁
├── time_period 规则          ├── 它的时效性
└── entities 提取规则         └── 它提到哪些实体
```

**已落地**：`tagger-worker.md` 的 "Your default job: APPLY the schema, not invent one" 一节。
Agent 遇到无法表达的内容时，标 `unknown` + 产出**变更提议**，绝不自行发明枚举值。

**仍缺**：存放 Schema 的地方，以及 `ragcli schema` 命令（见 P0-3）。

### 决策 2：Chunk 粒度是语料级，策略是文档级

```
语料级策略（固定）              文档级判断（Agent 决定）
├── 目标 chunk tokens 区间     ├── 用哪种切分策略
│   （由 embedding 模型定）     ├── 在哪里是安全边界
├── overlap 比例下限           ├── 哪些内容不能切
├── 必须保护的结构（代码/表格）  └── 整篇当一块还是拆
└── 必须携带的元数据字段
```

**已落地**：`chunk-worker.md` 的 "Policy vs judgment — the line you must not cross" 一节。
冲突时策略优先；策略导致某文档无法正确切分时，报为策略缺口而不是静默越界。

**仍缺**：策略文件的存放与加载（同 P0-3）。

---

## 七、建议的落地顺序

```
第 1 步：补契约（P0-1）
        contracts/*.schema.json + ragcli validate
        ← 这一步不做，后面全部互相猜格式

第 2 步：补 Manifest（P0-2）
        ragcli manifest init/show/update
        ← 有了它才能断点续跑和审计

第 3 步：补语料级 Schema / 策略的存放与加载（P0-3）
        corpus/schema.json + corpus/chunk-policy.json + ragcli schema

第 4 步：把手册里的断言变成可执行的质量门（P1-4）
        ragcli validate --stage X

第 5 步：增量更新 + 去重（P1-5, P1-6）

第 6 步：成本与可观测（P2-8, P2-10）
```

**别跳步。** 第 1、2、3 步是后面所有并行、重试、增量能力的前提——先有它们，再加 Worker，架构才不会塌。

---

## 八、一句话总结

现有资产是**四份高质量 Agent 手册 + 一个可靠的清洗工具 + 清晰的文档分层**。经过本次重构，**Agent 设计层面已经自洽**：拓扑一致、边界明确、手册与实现对齐。

剩下缺的全部是**基础设施的工具化**——手册已经描述清楚该做什么，但没有可执行的命令去强制它：

- **契约**（怎么交接不出错）→ `ragcli validate`
- **状态**（崩了怎么续、改了怎么增量）→ `ragcli manifest`
- **边界**（语料级策略放哪）→ `ragcli schema` / corpus 文件

Agent 设计上，核心是那个区分：**Type A 做薄（工具操作），Type B 做厚（判断），两者用独立 session 严格隔离。**
