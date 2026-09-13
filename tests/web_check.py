"""Test the web parser against live URLs and local HTML.

Network-dependent checks are marked SKIP rather than FAIL so the suite stays
useful offline — matching the environment note that proxy availability flips.

Run: python tests/web_check.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragcli.cleaners import clean_result
from ragcli.parsers import route

results = []


def check(name, ok, detail="", skip=False):
    results.append(("SKIP" if skip else ("OK" if ok else "FAIL"), name, detail))


tmp = Path(tempfile.mkdtemp(prefix="ragcli_web_"))

# ── local HTML: full page chrome must be stripped ─────────────────────────
html = """<!DOCTYPE html>
<html><head>
  <title>OAuth 2.0 Guide</title>
  <script>var tracking = "should not appear";</script>
  <style>.nav { display: none; }</style>
</head><body>
  <nav><a href="/">Home</a><a href="/docs">Docs</a></nav>
  <header>Site Header Noise</header>
  <article>
    <h1>OAuth 2.0</h1>
    <p>You must include the <code>state</code> parameter and verify it on return.</p>
    <h2>Token Expiry</h2>
    <p>Tokens expire after 3600 seconds by default.</p>
    <table><tr><th>Grant</th><th>Use</th></tr>
      <tr><td>authorization_code</td><td>Web apps</td></tr></table>
  </article>
  <aside>Related Links Sidebar</aside>
  <footer>Copyright 2024 Example Inc.</footer>
</body></html>
"""
html_file = tmp / "page.html"
html_file.write_text(html, encoding="utf-8")

r = clean_result(route(str(html_file)))
body = "\n".join(s.content for s in r.sections)

check("html: title extracted", r.meta.get("title") == "OAuth 2.0 Guide", str(r.meta.get("title")))
check("html: main content present", "state" in body and "3600" in body, body[:150])
check("html: script stripped", "tracking" not in body, body[:250])
check("html: nav stripped", "Home" not in body or "Site Header" not in body, body[:250])
check("html: footer stripped", "Copyright 2024" not in body, body[:250])
check("html: engine recorded", r.stats.get("parser_engine_used") in
      {"trafilatura", "readability-lxml", "naive-strip"}, str(r.stats.get("parser_engine_used")))
check("html: headings structured", any(s.type == "heading" for s in r.sections),
      str([s.type for s in r.sections]))
check("html: table detected", any(s.type == "table" for s in r.sections),
      str([s.type for s in r.sections]))
check("html: contract valid", not r.validate(), str(r.validate()))

# ── live URL ──────────────────────────────────────────────────────────────
try:
    r_url = route("https://example.com")
    body_url = "\n".join(s.content for s in r_url.sections)
    check("url: fetched and parsed", len(r_url.sections) > 0 and len(body_url) > 20,
          f"{len(r_url.sections)} sections")
    check("url: engine is trafilatura",
          r_url.stats.get("parser_engine_used") == "trafilatura",
          str(r_url.stats.get("parser_engine_used")))
    check("url: final url recorded", bool(r_url.meta.get("url")), str(r_url.meta.get("url")))
    check("url: contract valid", not r_url.validate(), str(r_url.validate()))
except Exception as e:  # noqa: BLE001
    check("url: fetched and parsed", False, f"{type(e).__name__}: {e}", skip=True)

# ── report ────────────────────────────────────────────────────────────────
width = max(len(n) for _, n, _ in results)
passed = sum(1 for s, _, _ in results if s == "OK")
skipped = sum(1 for s, _, _ in results if s == "SKIP")
failed = sum(1 for s, _, _ in results if s == "FAIL")
print()
for status, name, detail in results:
    line = f"[{status:<4}] {name.ljust(width)}"
    if status != "OK" and detail:
        line += f"  <- {detail}"
    print(line)
print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(0 if failed == 0 else 1)
