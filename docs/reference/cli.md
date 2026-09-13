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

Write path: raw source → normalized extracted structure

| tool | purpose |
|---|---|
| [`parse`](#parse) | Extract structured content from any source format (PDF, Word, Excel, PowerPoint, HTML/URL, Markdown, CSV, JSON, YAML, images) and emit a single normalized JSON contract for downstream tools |

### `retrieve`

Read path: query → ranked evidence

| tool | purpose |
|---|---|

### `evaluate`

Quality: measurement and regression detection

| tool | purpose |
|---|---|

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
