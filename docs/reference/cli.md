# CLI Reference

> **Generated from the live CLI** by `tests/gen_cli_reference.py`.
> Do not hand-edit — re-run the generator after changing any tool's arguments.

Every tool reads JSON from `-i/--input` (or stdin when omitted) and writes JSON to
`-o/--output` (or stdout). That uniformity is what makes them composable.

```bash
ragcli list                  # all tools
ragcli list --stage ingest   # only the write path
ragcli stages                # stage descriptions
ragcli <tool> --help         # any tool's full usage
```

## Pipeline stages

### `ingest`

Write path: raw source → parsed → cleaned → chunked → enriched → embedded → indexed

| tool | purpose |
|---|---|
| [`parse`](#parse) | Extract structured content from any source format (PDF, Word, Excel, PowerPoint, HTML/URL, Markdown, CSV, JSON, YAML, images) and emit a single normalized JSON contract for downstream tools |
| [`chunk`](#chunk) | Split documents into chunks for embedding and retrieval |
| [`summarize`](#summarize) | Generate short summaries for text chunks using LLM or local model |
| [`tagger`](#tagger) | Auto-classify and tag text chunks using LLM based on a predefined schema |
| [`embed`](#embed) | Generate vector embeddings for text chunks |
| [`index`](#index) | Build vector index from embedded chunks into a vector database |
| [`graph`](#graph) | Build a knowledge graph (nodes + edges) from text chunks for multi-hop reasoning |

### `retrieve`

Read path: query → ranked evidence

| tool | purpose |
|---|---|
| [`search`](#search) | Perform vector similarity search against a vector database |
| [`hybrid`](#hybrid) | Hybrid search combining semantic vector search and BM25 keyword matching using Reciprocal Rank Fusion |
| [`rerank`](#rerank) | Re-rank retrieved results using a Cross-Encoder model for better precision |
| [`cache`](#cache) | Semantic cache for RAG responses — look up by similarity or save new entries |

### `evaluate`

Quality: measurement and regression detection

| tool | purpose |
|---|---|
| [`evaluate`](#evaluate) | Evaluate RAG pipeline using RAG Triad metrics (context relevance, groundedness, answer relevance) |

---

## Tool reference

### parse

**Stage:** `ingest`  
**Purpose:** Extract structured content from any source format (PDF, Word, Excel, PowerPoint, HTML/URL, Markdown, CSV, JSON, YAML, images) and emit a single normalized JSON contract for downstream tools  
**Inputs:** file path, directory, or http(s) URL  
**Outputs:** ParsingResult JSON (sections with heading paths + provenance)

```text
usage: ragcli parse [-h] [-f FILE] [-d INPUT] [--no-recursive] [-o OUTPUT]
                    [--format {auto,json,jsonl}]
                    [--engine {auto,paddle,easyocr,tesseract}] [--lang LANG]
                    [--strategy {auto,text,ocr}] [--no-clean]
                    [--cleaners CLEANERS] [--min-chars MIN_CHARS]
                    [--merge-threshold MERGE_THRESHOLD]
                    [--boilerplate-ratio BOILERPLATE_RATIO] [--list-formats]
                    [--fail-fast]

Extract structured content from any source format (PDF, Word, Excel,
PowerPoint, HTML/URL, Markdown, CSV, JSON, YAML, images) and emit a single
normalized JSON contract for downstream tools

options:
  -h, --help            show this help message and exit

source:
  -f, --file FILE       A single file path or http(s) URL
  -d, -i, --input INPUT
                        A directory to batch-process (recurses by default)
  --no-recursive        Do not descend into subdirectories

output:
  -o, --output OUTPUT   Write result here (stdout if omitted)
  --format {auto,json,jsonl}
                        jsonl streams one document per line; auto picks based
                        on batch size

engine selection:
  --engine {auto,paddle,easyocr,tesseract}
                        Force a specific OCR engine for image sources
  --lang LANG           OCR language, e.g. ch / en / japan
  --strategy {auto,text,ocr}
                        PDF extraction strategy; 'ocr' skips native extraction
                        entirely

cleaning:
  --no-clean            Skip the cleaning pipeline
  --cleaners CLEANERS   Comma-separated subset to run, e.g.
                        'normalize,dehyphenate,dedupe'
  --min-chars MIN_CHARS
                        Drop blocks shorter than this
  --merge-threshold MERGE_THRESHOLD
                        Merge paragraph fragments shorter than this many
                        characters
  --boilerplate-ratio BOILERPLATE_RATIO
                        Fraction of blocks a repeated line must reach to be
                        dropped as boilerplate

misc:
  --list-formats        Print supported formats and whether their engine is
                        installed
  --fail-fast           Exit non-zero if any source failed
```

### chunk

**Stage:** `ingest`  
**Purpose:** Split documents into chunks for embedding and retrieval  
**Inputs:** documents (file or stdin)  
**Outputs:** chunks (JSON array)

```text
usage: ragcli chunk [-h] [-i INPUT] [-o OUTPUT] [--chunk-size CHUNK_SIZE]
                    [--chunk-overlap CHUNK_OVERLAP]
                    [--format {recursive,headers}]

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Input file (stdin if omitted)
  -o, --output OUTPUT   Output file (stdout if omitted)
  --chunk-size CHUNK_SIZE
                        Chunk size in characters (default: 512)
  --chunk-overlap CHUNK_OVERLAP
                        Overlap between chunks (default: 64)
  --format {recursive,headers}
                        Chunking strategy
```

### summarize

**Stage:** `ingest`  
**Purpose:** Generate short summaries for text chunks using LLM or local model  
**Inputs:** chunks (JSON array)  
**Outputs:** chunks with 'summary' field

```text
usage: ragcli summarize [-h] [-i INPUT] [-o OUTPUT] [--model MODEL] [--local]
                        [--max-tokens MAX_TOKENS] [--temperature TEMPERATURE]

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Input file (stdin if omitted)
  -o, --output OUTPUT   Output file
  --model MODEL         Model name
  --local               Use local model instead of API
  --max-tokens MAX_TOKENS
                        Max tokens for summary
  --temperature TEMPERATURE
                        Sampling temperature
```

### tagger

**Stage:** `ingest`  
**Purpose:** Auto-classify and tag text chunks using LLM based on a predefined schema  
**Inputs:** chunks (JSON array), tag schema (JSON)  
**Outputs:** chunks with 'tags' field

```text
usage: ragcli tagger [-h] [-i INPUT] [-o OUTPUT] --schema SCHEMA
                     [--model MODEL]

options:
  -h, --help           show this help message and exit
  -i, --input INPUT    Input file (stdin if omitted)
  -o, --output OUTPUT  Output file
  --schema SCHEMA      Tag schema file path or JSON string
  --model MODEL        Model name
```

### embed

**Stage:** `ingest`  
**Purpose:** Generate vector embeddings for text chunks  
**Inputs:** chunks (JSON array with 'text' field)  
**Outputs:** chunks with 'embedding' field

```text
usage: ragcli embed [-h] [-i INPUT] [-o OUTPUT] [--model MODEL]
                    [--api {local,openai}] [--base-url BASE_URL]
                    [--batch-size BATCH_SIZE]

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Input file (stdin if omitted)
  -o, --output OUTPUT   Output file (stdout if omitted)
  --model MODEL         Model name (default: BAAI/bge-m3)
  --api {local,openai}  Embedding backend
  --base-url BASE_URL   OpenAI-compatible API base URL
  --batch-size BATCH_SIZE
                        Batch size for local embedding
```

### index

**Stage:** `ingest`  
**Purpose:** Build vector index from embedded chunks into a vector database  
**Inputs:** embedded chunks (JSON array with 'embedding' field)  
**Outputs:** vector index created

```text
usage: ragcli index [-h] [-i INPUT] [--store {chroma,qdrant,milvus}]
                    [--collection COLLECTION] [--host HOST] [--port PORT]

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Input file (stdin if omitted)
  --store {chroma,qdrant,milvus}
                        Vector store backend
  --collection COLLECTION
                        Collection name
  --host HOST           Host for remote stores
  --port PORT           Port for remote stores
```

### graph

**Stage:** `ingest`  
**Purpose:** Build a knowledge graph (nodes + edges) from text chunks for multi-hop reasoning  
**Inputs:** tagged chunks (JSON array)  
**Outputs:** knowledge graph (JSON with nodes, edges)

```text
usage: ragcli graph [-h] [-i INPUT] [-o OUTPUT] [--model MODEL]

options:
  -h, --help           show this help message and exit
  -i, --input INPUT    Input file (stdin if omitted)
  -o, --output OUTPUT  Output file
  --model MODEL        LLM model for entity extraction
```

### search

**Stage:** `retrieve`  
**Purpose:** Perform vector similarity search against a vector database  
**Inputs:** query string, vector store config  
**Outputs:** matching chunks with scores (JSON)

```text
usage: ragcli search [-h] [-q QUERY] [-i INPUT] [-k TOP_K]
                     [--store {chroma,qdrant,milvus}]
                     [--collection COLLECTION] [--model MODEL]
                     [--api {local,openai}] [--base-url BASE_URL]
                     [--host HOST] [--port PORT]

options:
  -h, --help            show this help message and exit
  -q, --query QUERY     Search query string
  -i, --input INPUT     Input file with query JSON (stdin with '-')
  -k, --top-k TOP_K     Number of results (default: 5)
  --store {chroma,qdrant,milvus}
                        Vector store backend
  --collection COLLECTION
                        Collection name
  --model MODEL         Embedding model for query
  --api {local,openai}  Embedding backend
  --base-url BASE_URL   OpenAI-compatible API base URL
  --host HOST           Host for remote stores
  --port PORT           Port for remote stores
```

### hybrid

**Stage:** `retrieve`  
**Purpose:** Hybrid search combining semantic vector search and BM25 keyword matching using Reciprocal Rank Fusion  
**Inputs:** query string, embedded chunks (JSON)  
**Outputs:** merged results with RRF scores (JSON)

```text
usage: ragcli hybrid [-h] -q QUERY -i INPUT [-k TOP_K] [--model MODEL]
                     [--bm25-weight BM25_WEIGHT]
                     [--vector-weight VECTOR_WEIGHT]

options:
  -h, --help            show this help message and exit
  -q, --query QUERY     Search query
  -i, --input INPUT     Input embedded chunks JSON
  -k, --top-k TOP_K     Number of results
  --model MODEL         Embedding model
  --bm25-weight BM25_WEIGHT
                        BM25 weight in fusion
  --vector-weight VECTOR_WEIGHT
                        Vector weight in fusion
```

### rerank

**Stage:** `retrieve`  
**Purpose:** Re-rank retrieved results using a Cross-Encoder model for better precision  
**Inputs:** query string, retrieved results (JSON array)  
**Outputs:** re-ranked results with new scores (JSON)

```text
usage: ragcli rerank [-h] -q QUERY -i INPUT [-o OUTPUT] [--model MODEL]
                     [--backend {flag,st}] [--top-k TOP_K]

options:
  -h, --help           show this help message and exit
  -q, --query QUERY    Search query
  -i, --input INPUT    Input results JSON (stdin with '-')
  -o, --output OUTPUT  Output file
  --model MODEL        Cross-Encoder model
  --backend {flag,st}  Backend library
  --top-k TOP_K        Keep top K after re-ranking
```

### cache

**Stage:** `retrieve`  
**Purpose:** Semantic cache for RAG responses — look up by similarity or save new entries  
**Inputs:** query string, optional response to cache  
**Outputs:** cached response JSON or miss

```text
usage: ragcli cache [-h] [-q QUERY] [-i INPUT] [--store STORE] [--model MODEL]
                    [--threshold THRESHOLD] [--save] [--created-at CREATED_AT]

options:
  -h, --help            show this help message and exit
  -q, --query QUERY     Query string
  -i, --input INPUT     Input JSON with query/result (stdin with '-')
  --store STORE         Cache file path
  --model MODEL         Embedding model
  --threshold THRESHOLD
                        Similarity threshold for hit
  --save                Save result to cache
  --created-at CREATED_AT
                        Timestamp for cache entry
```

### evaluate

**Stage:** `evaluate`  
**Purpose:** Evaluate RAG pipeline using RAG Triad metrics (context relevance, groundedness, answer relevance)  
**Inputs:** golden dataset, search/generation results  
**Outputs:** evaluation metrics (JSON)

```text
usage: ragcli evaluate [-h] -g GOLDEN -r RESULTS [-o OUTPUT] [--model MODEL]

options:
  -h, --help            show this help message and exit
  -g, --golden GOLDEN   Golden dataset JSON
  -r, --results RESULTS
                        Results JSON
  -o, --output OUTPUT   Output file
  --model MODEL         Judge model
```
