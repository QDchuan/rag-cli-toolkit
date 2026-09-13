# RAG 管线阶段依赖关系与并行优化

> **本文档说明各阶段的依赖关系、可并行性、以及不同场景下的最优执行顺序。**

---

## 一、阶段依赖总览

### 1.1 依赖关系图

```
                    ┌──────────┐
                    │ raw file │
                    └────┬─────┘
                         │ 必须
                    ┌────▼─────┐
                    │  PARSE   │ ← 第一步，无可替代
                    └────┬─────┘
                         │ 必须
                    ┌────▼─────┐
                    │  CLEAN   │ ← 可选但推荐
                    └────┬─────┘
                         │ 必须
                    ┌────▼─────┐
                    │  CHUNK   │ ← 核心步骤，无可替代
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         ┌────▼───┐ ┌───▼────┐ ┌──▼──────┐
         │SUMMARIZE│ │TAGGER  │ │EMBED    │ ← 这三个可以并行！
         └────┬───┘ └───┬────┘ └──┬──────┘
              │          │        │
              │          │     ┌──▼──────┐
              │          │     │ INDEX   │ ← 必须等 embed 完成
              │          │     └────┬────┘
              │          │          │
              │          │     ┌────▼────┐
              │          │     │ SEARCH  │ ← 必须等 index 完成
              │          │     └────┬────┘
              │          │          │
              │          │     ┌────▼────┐
              │          │     │ RERANK  │ ← 必须等 search 完成
              │          │     └────┬────┘
              │          │          │
              │          │     ┌────▼────┐
              │          │     │ ANSWER  │ ← 最终生成
              │          │     └─────────┘
```

### 1.2 硬性依赖（必须按顺序）

| 前置阶段 | 后续阶段 | 原因 |
| --- | --- | --- |
| 无 | **parse** | parse 是入口，无可替代 |
| parse | **clean** | clean 需要 parse 输出的纯文本 |
| clean | **chunk** | chunk 需要干净的文本内容 |
| chunk | **embed** | embed 需要 chunk 的 text 字段 |
| embed | **index** | index 需要 embedding 字段 |
| index | **search** | search 需要从索引中查询 |
| search | **rerank** | rerank 需要对检索结果重排序 |
| search | **answer** | answer 需要检索结果作为上下文 |

### 1.3 可并行阶段

| 并行组 | 说明 |
| --- | --- |
| **summarize / tagger / embed** | 三者都只依赖 chunk 的输出，互不依赖，可并行执行 |
| **search / hybrid** | 两者都依赖 index（或 embed），互不依赖，可并行 |

### 1.4 可选跳过阶段

| 阶段 | 何时跳过 |
| --- | --- |
| **clean** | 文档本身干净（Markdown、生成的文档） |
| **summarize** | 文档很短（< 1000 字）、预算紧张 |
| **tagger** | 不需要分类过滤、知识库简单 |
| **hybrid** | 只用向量搜索就够了 |
| **rerank** | 对精度要求不高、延迟敏感 |
| **graph** | 不需要多跳推理 |

---

## 二、不同场景的最优管线

### 场景 A：快速入库（追求速度）

```
parse → chunk → embed → index → search
```

- **耗时**：最短
- **适用**：原型验证、内部工具、FAQ 机器人
- **跳过**：clean, summarize, tagger, hybrid, rerank, graph

### 场景 B：标准生产（平衡速度与质量）

```
parse → clean → chunk → [summarize ║ tagger ║ embed] → index → search
```

- **耗时**：中等
- **适用**：大多数企业知识库
- **并行**：summarize / tagger / embed 三线程并行

### 场景 C：高精度检索（追求质量）

```
parse → clean → chunk → [summarize ║ tagger ║ embed] → index → [search ║ hybrid] → rerank → answer
```

- **耗时**：较长
- **适用**：法律、医疗、金融等专业领域
- **并行**：summarize/tagger/embed 并行；search/hybrid 并行

### 场景 D：知识图谱增强（多跳推理）

```
parse → clean → chunk → tagger → graph
                                        → embed → index → search → rerank → answer
```

- **耗时**：最长
- **适用**：复杂关系查询、"A 的供应商的客户是谁"类问题
- **注意**：graph 和 embed 可以并行（都只依赖 chunk + tagger）

### 场景 E：实时流式更新

```
parse → clean → chunk → embed → [增量索引] → search
```

- **特点**：每处理一个 chunk 就立即增量索引，不等全部完成
- **适用**：客服系统、实时新闻

---

## 三、Agent 决策树

Agent 收到新资料后，按以下流程决定管线：

```
收到文档
│
├─ Q1: 文档是否干净？（Markdown/生成的文档 vs PDF/网页/OCR）
│   ├─ 干净 → 跳过 clean
│   └─ 不干净 → 执行 clean
│
├─ Q2: 文档长度？
│   ├─ < 1000 字 → 跳过 summarize
│   └─ ≥ 1000 字 → 执行 summarize
│
├─ Q3: 知识库是否需要分类过滤？
│   ├─ 是 → 执行 tagger
│   └─ 否 → 跳过 tagger
│
├─ Q4: 检索精度要求？
│   ├─ 低（原型/内部）→ 只用 search
│   └─ 高（生产/专业）→ 同时用 search + hybrid → rerank
│
├─ Q5: 需要多跳推理吗？
│   ├─ 是 → 执行 graph（与 embed 并行）
│   └─ 否 → 跳过 graph
│
└─ Q6: 预算限制？
    ├─ 紧张 → 跳过 summarize, 用本地模型 embed
    └─ 充足 → 全量执行
```

---

## 四、并行执行示例

### 方式 1：Shell 后台任务

```bash
# 串行部分
ragcli parse -f doc.pdf -o parsed.json
ragcli clean -i parsed.json -o cleaned.json
ragcli chunk -i cleaned.json -o chunks.json

# 并行部分（三个命令同时跑）
ragcli summarize -i chunks.json -o summarized.json &
ragcli tagger -i chunks.json -o tagged.json --schema tags.json &
ragcli embed -i chunks.json -o embedded.json --model BAAI/bge-m3 &

# 等待所有后台任务完成
wait

# 合并结果（如果需要 combine）
# ...
```

### 方式 2：Python 多线程

```python
import subprocess
from concurrent.futures import ThreadPoolExecutor

def run_cmd(cmd):
    subprocess.run(cmd, shell=True)

# 串行
run_cmd("ragcli parse -f doc.pdf -o parsed.json")
run_cmd("ragcli clean -i parsed.json -o cleaned.json")
run_cmd("ragcli chunk -i cleaned.json -o chunks.json")

# 并行
with ThreadPoolExecutor(max_workers=3) as executor:
    executor.submit(run_cmd, "ragcli summarize -i chunks.json -o summarized.json")
    executor.submit(run_cmd, "ragcli tagger -i chunks.json -o tagged.json --schema tags.json")
    executor.submit(run_cmd, "ragcli embed -i chunks.json -o embedded.json --model BAAI/bge-m3")

# 继续串行
run_cmd("ragcli index -i embedded.json -d qdrant --collection docs")
```

### 方式 3：JSON 管线（stdin/stdout 直接传递）

```bash
# 最简洁的方式——不需要中间文件
ragcli parse -f doc.pdf \
  | ragcli clean \
  | ragcli chunk \
  | tee chunks.json > /dev/null &
  
# 然后多个消费者从 chunks.json 读取
ragcli summarize -i chunks.json -o summarized.json &
ragcli tagger -i chunks.json -o tagged.json &
ragcli embed -i chunks.json -o embedded.json &
wait
```

---

## 五、注意事项

### 5.1 资源竞争

并行执行时注意：
- **LLM API 限流**：summarize + tagger + embed 同时调 OpenAI 可能触发 rate limit
  - 解决：加 delay 或使用本地模型
- **磁盘 I/O**：大量写入 JSON 文件可能阻塞
  - 解决：使用 stdin/stdout 管道代替临时文件
- **GPU 内存**：本地 embed + rerank 同时运行可能 OOM
  - 解决：串行执行 GPU 密集型任务

### 5.2 数据一致性

- 并行执行的阶段必须保证**输入数据不变**（不要在并行期间修改 chunks.json）
- 如果某个并行阶段失败，整个管线应回滚或重试

### 5.3 进度追踪

Agent 应记录每个阶段的：
- 开始时间、结束时间
- 处理的 chunk 数量
- 错误信息（如有）

这样可以在某个阶段失败时精确定位。

---

## 六、总结速查表

| 操作 | 类型 | 说明 |
| --- | --- | --- |
| parse | 🔴 必须第一个 | 无可替代的入口 |
| clean | 🟡 可选 | 视文档质量而定 |
| chunk | 🔴 必须在 clean 之后 | 无可替代的核心步骤 |
| summarize | 🟢 可并行 | 与 tagger/embed 并行 |
| tagger | 🟢 可并行 | 与 summarize/embed 并行 |
| embed | 🟢 可并行 | 与 summarize/tagger 并行 |
| index | 🔴 必须在 embed 之后 | 依赖 embedding 数据 |
| search | 🟢 可并行 | 与 hybrid 并行 |
| hybrid | 🟢 可并行 | 与 search 并行 |
| rerank | 🔴 必须在 search 之后 | 依赖检索结果 |
| graph | 🟢 可并行 | 与 embed 并行（都依赖 chunk+tagger） |
| evaluate | 🟡 最后执行 | 依赖 golden dataset + 检索结果 |
