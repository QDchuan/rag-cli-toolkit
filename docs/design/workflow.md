# 数据清洗工作流

> **这份文档定义整个工作流。** 先想清楚流程，再改代码。
>
> 读者：项目决策者与编排 Agent。工具的使用细节在 [`../reference/cli.md`](../reference/cli.md)；各 worker 的判断依据在各自的 `../agents/` 手册。

---

## 一、要解决的问题

用户往知识库塞资料——PDF、图片、网址、Word、Excel——**清洗应该自动完成，Agent 参与其中，而不是人工逐步操作**。

清洗的产物必须支撑两种截然不同的提问：

| 提问方式 | 例子 | 需要的产物 |
|---|---|---|
| **精确查询** | 「第 7 条的终止条件是什么」 | chunk 级细粒度单元，可向量匹配 |
| **综合查询** | 「我们在供应商管理上有哪些风险」 | **文档级摘要**，可被批量扫读 |

**这套工作流的全部设计都从这两行推导出来。**

---

## 二、两层产物 —— 这是核心

### 综合查询靠什么工作

面对「我们在供应商管理上有哪些风险」，如果只有 chunks，Agent 只能靠向量相似度去撞——它不知道库里有哪几篇文档、每篇讲什么，于是既容易漏，也容易撞进一堆无关片段。

有了**文档级摘要**，路径就变了：

```
综合问题
   ↓
扫一遍 catalog.jsonl（所有文档的摘要）
   ↓  纯文本判断，很快
选出可能相关的 3-5 篇
   ↓
在这几篇的 chunks 里做细查
   ↓
综合回答
```

**这条路径可行的前提是摘要足够短**——短到能一次全部过完。所以摘要长度不是文风问题，是**语料规模的函数**（见第七节）。

### 两层各自的职责

| | 文档级摘要（粗层） | chunks（细层） |
|---|---|---|
| **粒度** | 一篇文档一条 | 一篇文档几十条 |
| **用途** | 批量扫读，判断「要不要读这篇」 | 精确匹配，定位到具体段落 |
| **怎么用** | 一次全读，做相关性筛选 | 向量相似度检索 |
| **必须带** | `title` + `topics` + 判别性描述 | `source_id` 指回所属文档 |
| **不需要** | — | 各自的摘要 |

**「不需要 chunk 级摘要」是刻意的。** chunk 已经有正文可供向量匹配；给它再加一层摘要，既增加成本，又和文档级摘要的职责重叠。

### 连接两层的字段

`chunks[].source_id` 必须等于文档摘要的 `source_id`。**这个字段断了，「先摘要、后细查」的两段式路径就断了**，而这是综合查询的唯一通道。

---

## 三、工作流全景

```
原始资料（PDF / 图片 / 网址 / Word / Excel / …）
        │
        ▼
   ┌─────────┐
   │ 1 接收  │  展开目录、识别 URL、生成待处理清单
   └────┬────┘
        ▼
   ┌──────────────┐
   │ 2 抽取 + 清洗 │  ragcli parse（7 类引擎 + 9 步清洗）
   └────┬─────────┘
        │ parsed.json
        ▼
   ┌─────────┐
   │ 3 质检  │  verdict: ok / degraded / unusable
   └────┬────┘
        │
        ├──── unusable ──→ 人工复核队列（不进后续）
        │
        ├────────────────┬─────────────────┐
        ▼                ▼                 │
   ┌─────────┐     ┌──────────┐            │  并行
   │ 4 摘要  │     │ 5 分块    │            │
   └────┬────┘     └────┬─────┘            │
        │ doc-summary   │ chunks           │
        └───────┬───────┘                  │
                ▼                          │
          ┌──────────┐                     │
          │ 6 编目    │ ←───────────────────┘
          └────┬─────┘
               ▼
        catalog.jsonl  ← 综合查询的入口
        chunks.json    ← 精确查询的入口
```

---

## 四、阶段详解

| # | 阶段 | 输入 | 产出 | 谁做 | 判断性质 |
|---|---|---|---|---|---|
| 1 | **接收** | 文件 / 目录 / URL / 混合批次 | 待处理清单 | 编排 Agent | 无判断 |
| 2 | **抽取 + 清洗** | 单个原始资料 | `parsed.json` | `ragcli parse` | 无判断（格式问题已由引擎解决） |
| 3 | **质检分流** | `parsed.json` | `verdict` + 原因 | 代码产出，编排 Agent 读 | 确定性规则 |
| 4 | **文档摘要** | `parsed.json` | `doc-summary.json` | Summarize Worker | **判断**：怎么概括才有判别性 |
| 5 | **分块** | `parsed.json` | `chunks.json` | Chunk Worker | **判断**：哪里能切、哪里不能切 |
| 6 | **编目** | 上述产物 | `catalog.jsonl` | 编排 Agent | 无判断（汇总） |

**阶段 4 与 5 并行**——都只依赖 `parsed.json`，互不依赖。

**注意阶段 4 与 5 没有工具。** 这不是缺失，是设计：没有固定 CLI 能表达「这份文档哪里不能切」或「怎么概括才让读者判断得出相关性」。这两个 worker **自己写代码**。

---

## 五、产物契约

### 5.1 `parsed.json`（阶段 2 产出，已实现）

```jsonc
{
  "source_id": "report_a1b2c3d4",
  "source_path": "raw/report.pdf",
  "format_type": "pdf",
  "meta": {"title": "...", "page_count": 42, "word_count": 18000},
  "sections": [{
    "block_id": 0,
    "type": "heading",              // heading|paragraph|table|list_item|code_block
    "heading_path": ["第三章", "3.1 准入标准"],
    "content": "……",
    "level": 2,
    "location": {"page": 15},
    "metadata": {}
  }],
  "stats": {
    "parser_engine_used": "pdfplumber",
    "warnings": [],
    "cleaning": {"blocks_before": 210, "blocks_after": 188, "blocks_removed": 22}
  },
  "verdict": "ok",                  // 新增，见第六节
  "verdict_reasons": []
}
```

### 5.2 `doc-summary.json`（阶段 4 产出，粗层）

```jsonc
{
  "summary_contract": "1.0",
  "source_id": "report_a1b2c3d4",
  "source_path": "raw/report.pdf",
  "title": "2024 年度供应商管理报告",
  "summary": "2024 年供应商准入标准调整，新增账期风险条款，附 12 家供应商的合规审计结果与三家降级处理。",
  "topics": ["供应商准入", "账期风险", "合规审计"],
  "token_count": 98,
  "meta": {"doc_type": "report", "language": "zh", "page_count": 42}
}
```

**三条硬约束：**

1. **必须短** —— 长度由语料规模反推（第七节）
2. **必须有判别性** —— 判据是：**没读过原文的人，只看摘要能否决定「要不要读这篇」**
   - ❌「本文介绍了供应商管理的相关内容」—— 任何一篇都能这么写，零信息
   - ✅「2024 年供应商准入标准调整，新增账期风险条款，附 12 家供应商合规审计结果」—— 可据此判断相关性
3. **必须带 `title` 与 `topics`** —— 摘要是被**批量扫**的，这两项是长列表里的筛选抓手

### 5.3 `chunks.json`（阶段 5 产出，细层）

```jsonc
{
  "chunk_contract": "1.0",
  "source_id": "report_a1b2c3d4",        // ← 工件级：必须与摘要一致
  "source_path": "raw/report.pdf",
  "chunks": [{
    "chunk_id": "report_a1b2c3d4::3::9f2c1a77",   // 稳定唯一，富化阶段靠它关联
    "source_id": "report_a1b2c3d4",               // ← 每个 chunk 也要带
    "chunk_index": 3,
    "text": "第三章 供应商准入 > 3.1 准入标准\n\n……",   // 含面包屑，供向量匹配
    "raw_text": "……",                                    // 纯净正文，供生成
    "heading_path": ["第三章 供应商准入", "3.1 准入标准"],
    "location": {"page": 15},
    "token_count": 340
  }],
  "stats": {"total_chunks": 42, "decision": {...}}
}
```

**为什么 `source_id` 出现两次（工件级 + 每个 chunk）**：入库时所有文档的 chunks 会被**汇到一个向量索引里**。那时工件级的 `source_id` 已经不在了，每个 chunk 必须能自证来自哪篇——否则检索命中了却说不出来源，两段式路径的「回溯到文章」这一步就断了。

工件级保留它是为了让单文档产物自洽；chunk 级保留它是为了合并后仍然自洽。

### 5.4 `catalog.jsonl`（阶段 6 产出，综合查询的入口）

一行一篇文档。**这个文件就是「所有摘要」的物理形态**——综合查询时读它即可，不必遍历目录。

```jsonc
{"source_id":"report_a1b2c3d4","title":"2024 年度供应商管理报告","summary":"……","topics":["供应商准入","账期风险"],"status":"ready","chunk_count":42,"token_count":98,"processed_at":"..."}
{"source_id":"policy_b5c6d7e8","title":"采购合规管理办法","summary":"……","topics":["采购流程","审批权限"],"status":"ready","chunk_count":18,"token_count":76,"processed_at":"..."}
```

`status` 取值：`ready` / `degraded` / `unusable`（与 verdict 对应，便于筛出需人工处理的条目）。

---

## 六、质量判定与分流

`parse` 产出一个确定性判定，让分流不依赖 Agent 的主观解读：

| verdict | 判据 | 后续动作 |
|---|---|---|
| `ok` | 有 sections，无 warning | 正常进摘要 + 分块 |
| `degraded` | 有 sections，但有 warning（OCR 触发、表格检测失败、清洗删除过多） | 进摘要 + 分块，**并在 catalog 标记供人工抽查** |
| `unusable` | 0 sections，或每页字符数低于阈值，或文件损坏 | **不进后续**，进人工复核队列 |

判定规则（确定性，写在代码里）：

```
if len(sections) == 0:                             unusable  "no sections extracted"
elif pages > 1 and chars_per_page < 25:            unusable  "too little text, likely scanned (install OCR)"
elif warnings or blocks_removed > 50% of blocks:   degraded  <具体原因>
else:                                              ok
```

**分流的意义**：一份扫描件 PDF 如果 OCR 没装，它会产出 0 个 section。若不判定，它会带着空内容一路走完摘要与分块，最后在 catalog 里伪装成一篇正常文档——**这比直接失败更糟**。

---

## 七、语料级策略

放在 `corpus/`，进 git，改动记版本号：

```
corpus/
├── cleaning-policy.json    # 清洗规则开关与阈值
├── summary-budget.json     # 摘要长度上限（由语料规模反推）
└── tag-schema.json         # 标签维度（若需要过滤检索）
```

### 摘要长度的算术约束

综合查询的可行性 = `文档数 × 单篇摘要长度`：

| 语料规模 | 摘要长度 | 全量扫描成本 | 可行？ |
|---|---|---|---|
| 1,000 篇 | 100 tokens | 100k tokens | ✅ 一次读完 |
| 5,000 篇 | 100 tokens | 500k tokens | ⚠️ 需要分片扫描 |
| 10,000 篇 | 100 tokens | 1M tokens | ❌ 需要先按 topics 预筛 |
| 10,000 篇 | 50 tokens | 500k tokens | ⚠️ 勉强 |

**结论：摘要长度是语料规模的函数，不是风格选择。**

超过约 5,000 篇时，单靠「全量扫摘要」不再可行，需要引入「按 `topics` 先过滤」或「摘要的摘要」。**这也是为什么现在就要产出 `topics` 字段**——为那条路留好接口。

---

## 八、失败处理

| 情况 | 处理 |
|---|---|
| 缺解析引擎 | `verdict = unusable`，原因写明 `pip install ...`，进复核队列；不中断批次 |
| 文件损坏 / 加密 | `unusable`，原因写明；进复核队列 |
| 网址抓取失败（404/超时） | 记失败，不中断批次，可单独重试 |
| OCR 未安装且是扫描件 | `unusable`，提示安装；**绝不放行空内容** |
| 摘要为空或超长 | 校验失败，把问题回传同一 worker 重写（最多 2 次） |
| chunks 的 `source_id` 与摘要不一致 | 校验失败（两段式路径会断） |
| 同一文档重复塞入 | 按 `source_path` + 内容哈希识别，提示已存在 |
| 文档更新后重新塞入 | 摘要与 chunks 全部作废重做（增量留待下一阶段） |

---

## 九、实现

工作流由三部分组成：

| 部分 | 是什么 | 在哪 |
|---|---|---|
| **调度器** | 确定性的 workflow 脚本 | `workflows/clean-corpus.js` |
| **worker 手册** | 每个 worker 的判断依据 | `docs/agents/*-worker.md` |
| **语料级策略** | 切块边界与摘要语言 | `<corpus>/corpus/*.json` |

### 为什么调度器是代码而不是 Agent

「下一步做什么、能不能并行、闸门过没过」都是确定性的。写成提示词有两个后果：不可复现，以及把调度知识塞进一个**本该对格式和切块边界一无所知**的上下文里。

所以 `workflows/clean-corpus.js` 承担编排，它是 DSH `workflow` 工具的脚本体：`parse → 质量闸 → [chunk ∥ summarize] → verify → catalog`。

### 两个从真实运行中学到的约束

**1. 脚本没有 shell，所以每一步都是 `agent()` 调用**

workflow 脚本没有文件系统、网络和 shell，所以连 `ragcli parse` 也得交给一个 agent 去跑。worker 提示词因此保持**极短**——只给任务信封和手册路径，判断依据全在手册里。把手册内容抄进提示词会重新制造这个项目一直在对抗的文档漂移。

**2. 永远不要相信 worker 的自述**

第一次真实运行暴露了这个：两个 worker **把活干对了**（磁盘上 `chunks.json` 和 `doc-summary.json` 都完整有效），但它们的最终 JSON 回复没通过 schema 校验，于是 `agent()` 解析为 null，编排器把两个成功报成了失败——**少报了 3 倍**。

所以脚本不消费 worker 的回复。它在 enrich 之后派一个**独立的 verify agent** 去读文件系统，报告实际存在什么。**catalog 是从核实过的文件内容构建的，不是从 worker 声称的内容。**

### 语料级策略文件

```
<corpus>/corpus/
├── summary-budget.json     # 摘要长度上限 + summary_language
└── chunk-policy.json       # token 区间 / overlap / 保护结构
```

两份都是**语料级决策**，不是逐文档的：

- `summary_language` 是**用户提问的语言**，不是源文档的语言。混语言的目录是半坏的——用任一语言提问都会静默漏掉一部分语料。第一次真实运行就踩到了：四份英文语料产出两份中文摘要和一份英文摘要，因为手册当时没规定语言策略，每个 worker 各自猜，猜得还不一样。
- `token_bounds` 由 embedding 模型决定，不该每篇文档重新发明。

---

## 十、边界

### 做

- 任意格式 → 统一的 `parsed.json`
- 自动清洗（9 步管线，内置在 `parse` 里）
- 质量判定与分流
- 文档级摘要（粗层）
- chunks（细层）
- 语料目录 `catalog.jsonl`

### 不做

- **不实现检索**（向量库、相似度搜索、rerank）。本工作流只保证产出物**支撑得了**那两种提问方式
- **不实现向量化与入库**。`chunks.json` 是交付终点
- **不给摘要与分块做工具**。它们的难点是判断，不是格式，由 worker 写代码
- **不做增量更新与语料级去重**（下一阶段）

---

## 十一、验收标准

工作流跑通的判据，是**两条提问路径都成立**：

1. **精确路径**：给一个具体条款问题，能在 `chunks.json` 中定位到正确段落
2. **综合路径**：给一个综合性问题，**只读 `catalog.jsonl`** 就能选出正确的文档集合，不需要读 chunks

再加：

3. `ragcli parse` 对任意文档输出 `verdict`，三种取值都有测试覆盖
4. 一份混合语料（PDF + 图片 + 网址）从原始到 catalog 条目全程走通
5. 摘要的判别性通过抽查：遮住原文，只看摘要能判断相关性
