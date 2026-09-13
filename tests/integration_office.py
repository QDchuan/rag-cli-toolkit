"""Integration test: generate real Office/PDF files and verify extraction quality.

This exercises the paths that unit tests with fake bytes cannot reach — actual
heading levels in DOCX, merged cells and multi-sheet in XLSX, table detection in
PDF. Skipped gracefully when a generator library is unavailable.

Run:  python tests/integration_office.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile

from ragcli.cleaners import clean_result
from ragcli.parsers import route


def parse_and_clean(src: str, **clean_opts):
    """Mirror what `ragcli parse` does: extract, then run the cleaning pipeline.

    The two steps are deliberately separate in the API (parse is pure extraction,
    cleaning is composable), so tests must apply both to match real behaviour.
    """
    return clean_result(route(src), clean_opts or None)

results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((ok, name, detail))


tmp = Path(tempfile.mkdtemp(prefix="ragcli_office_"))

# ── DOCX: real heading styles, nested list, a table ───────────────────────
try:
    import docx  # type: ignore

    doc = docx.Document()
    doc.add_heading("Quarterly Report", level=1)
    doc.add_paragraph("This paragraph precedes any subsection and carries body text.")
    doc.add_heading("Revenue", level=2)
    doc.add_paragraph("Revenue grew across all regions this quarter.")
    doc.add_heading("By Region", level=3)
    doc.add_paragraph("EMEA led growth followed by APAC.", style="List Bullet")

    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Revenue"
    table.cell(1, 0).text = "EMEA"
    table.cell(1, 1).text = "1.2M"
    table.cell(2, 0).text = "APAC"
    table.cell(2, 1).text = "0.9M"

    doc.add_heading("Outlook", level=2)
    doc.add_paragraph("Guidance remains unchanged for the next fiscal year.")

    docx_path = tmp / "report.docx"
    doc.save(str(docx_path))

    r = route(str(docx_path))
    body = "\n".join(s.content for s in r.sections)
    headings = [s for s in r.sections if s.type == "heading"]
    tables = [s for s in r.sections if s.type == "table"]
    list_items = [s for s in r.sections if s.type == "list_item"]

    check("docx: extracted content", len(r.sections) >= 6, f"{len(r.sections)} sections")
    check("docx: real heading levels", [h.level for h in headings] == [1, 2, 3, 2],
          str([h.level for h in headings]))
    check("docx: table extracted as markdown", len(tables) == 1 and "| EMEA | 1.2M |" in tables[0].content,
          tables[0].content[:100] if tables else "no table")
    check("docx: list item typed correctly", len(list_items) == 1, str(len(list_items)))

    # The critical assertion: a block under "By Region" must carry the full chain
    deep = [s for s in r.sections if "EMEA led growth" in s.content]
    check(
        "docx: heading_path has full ancestor chain",
        bool(deep) and deep[0].heading_path == ["Quarterly Report", "Revenue", "By Region"],
        str(deep[0].heading_path) if deep else "block not found",
    )
    check("docx: contract valid", not r.validate(), str(r.validate()))
except ImportError:
    check("docx: skipped (python-docx not installed)", True)

# ── XLSX: multiple sheets, merged cells, numeric cells ───────────────────
try:
    import openpyxl  # type: ignore

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Summary"
    ws1["A1"] = "Metric"
    ws1["B1"] = "Value"
    ws1["A2"] = "Revenue"
    ws1["B2"] = 1200
    # Merged cell across A3:B3 — only the top-left holds the value
    ws1["A3"] = "Combined total"
    ws1.merge_cells("A3:B3")

    ws2 = wb.create_sheet("Detail")
    ws2.append(["Item", "Qty"])
    ws2.append(["Widget", 5])
    ws2.append(["Gadget", 7])

    xlsx_path = tmp / "book.xlsx"
    wb.save(str(xlsx_path))

    r = route(str(xlsx_path))
    tables = [s for s in r.sections if s.type == "table"]
    headings = [s for s in r.sections if s.type == "heading"]

    check("xlsx: both sheets present", len(tables) == 2, f"{len(tables)} tables")
    check("xlsx: sheet headings", len(headings) == 2, str([h.content for h in headings]))
    check("xlsx: numeric cell preserved", any("1200" in t.content for t in tables),
          str([t.content[:60] for t in tables]))

    summary_table = next((t for t in tables if t.location.get("sheet") == "Summary"), None)
    check(
        "xlsx: merged cell value expanded to covered cell",
        summary_table is not None and summary_table.content.count("Combined total") >= 2,
        summary_table.content if summary_table else "no Summary table",
    )
    check("xlsx: merged range counted", summary_table is not None
          and summary_table.metadata.get("merged_ranges_expanded") == 1,
          str(summary_table.metadata) if summary_table else "")
    check("xlsx: contract valid", not r.validate(), str(r.validate()))
except ImportError:
    check("xlsx: skipped (openpyxl not installed)", True)

# ── PDF: author a minimal valid PDF by hand (no reportlab needed) ────────
# pdfplumber depends on pdfminer.six, which only *reads* PDFs. Rather than skip
# the most important format, we emit a minimal spec-compliant PDF ourselves.
# The xref table requires exact byte offsets, so we build the body first and
# record positions as we go.


def make_pdf(path: Path, pages: list[list[str]], footer: str | None = None) -> None:
    """Write a minimal text-only PDF. One page per entry in `pages`."""
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)  # 1-based object number

    font_num = 1
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Reserve catalog and pages objects; fill them after page objects are known
    objects.append(b"")  # placeholder, becomes catalog
    objects.append(b"")  # placeholder, becomes pages

    page_nums: list[int] = []
    for lines in pages:
        content_lines = ["BT", "/F1 12 Tf", "72 750 Td", "14 TL"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            content_lines.append(f"({escaped}) Tj T*")
        if footer:
            escaped = footer.replace("(", r"\(").replace(")", r"\)")
            content_lines.append("ET")
            content_lines.append("BT /F1 9 Tf 72 60 Td")
            content_lines.append(f"({escaped}) Tj")
            content_lines.append("ET")
        else:
            content_lines.append("ET")

        stream = "\n".join(content_lines).encode("latin-1")
        content_num = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_num = add(
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] "
            b"/Contents " + str(content_num).encode() + b" 0 R "
            b"/Resources << /Font << /F1 1 0 R >> >> >>"
        )
        page_nums.append(page_num)

    objects[1] = b"<< /Type /Catalog /Pages 3 0 R >>"
    kids = b" ".join(str(n).encode() + b" 0 R" for n in page_nums)
    objects[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_nums)).encode() + b" >>"
    )

    # Serialize with a correct cross-reference table
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"

    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()

    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 2 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    path.write_bytes(bytes(out))


try:
    pdf_path = tmp / "doc.pdf"
    make_pdf(
        pdf_path,
        pages=[
            ["Section One", "This is the first paragraph of the document body.",
             "It continues here with additional detail for retrieval."],
            ["Section Two", "The second page carries a different topic entirely."],
            ["Section Three", "Final page content for multi-page extraction."],
        ],
        footer="CONFIDENTIAL DRAFT",
    )

    r = parse_and_clean(str(pdf_path))
    body = "\n".join(s.content for s in r.sections)
    check("pdf: native text extracted", "first paragraph" in body, body[:200] or "(empty)")
    check("pdf: page count from metadata", r.meta.get("page_count") == 3,
          str(r.meta.get("page_count")))
    check("pdf: reported as not scanned", r.meta.get("is_scanned") is False,
          str(r.meta.get("is_scanned")))
    check("pdf: engine is pdfplumber", r.stats.get("parser_engine_used") == "pdfplumber",
          str(r.stats.get("parser_engine_used")))
    check("pdf: provenance page numbers present",
          all("page" in s.location for s in r.sections),
          str([s.location for s in r.sections[:3]]))
    check("pdf: repeated footer stripped as boilerplate",
          "CONFIDENTIAL DRAFT" not in body, body[:300])
    check("pdf: contract valid", not r.validate(), str(r.validate()))
    check("pdf: all three pages represented",
          len({s.location.get("page") for s in r.sections}) == 3,
          str(sorted({s.location.get("page") for s in r.sections})))
except Exception as e:  # noqa: BLE001
    check("pdf: test harness failed", False, f"{type(e).__name__}: {e}")

# ── report ────────────────────────────────────────────────────────────────
width = max(len(n) for _, n, _ in results)
passed = sum(1 for ok, _, _ in results if ok)
print()
for ok, name, detail in results:
    mark = "OK  " if ok else "FAIL"
    line = f"[{mark}] {name.ljust(width)}"
    if not ok and detail:
        line += f"  <- {detail}"
    print(line)
print(f"\n{passed}/{len(results)} checks passed")
print(f"fixtures kept in: {tmp}")
sys.exit(0 if passed == len(results) else 1)
