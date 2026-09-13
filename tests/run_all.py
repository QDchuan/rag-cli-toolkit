"""Run every parse-module test suite in sequence.

Usage:  python tests/run_all.py
Exits non-zero if any suite fails, so it works as a CI gate.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    ("smoke", HERE / "smoke_parse.py", "Contract, cleaning, error handling"),
    ("integration", HERE / "integration_office.py", "Real DOCX/XLSX/PDF extraction"),
    ("web", HERE / "web_check.py", "HTML/URL extraction (network optional)"),
    ("verdict", HERE / "test_verdict.py", "Quality verdict: ok / degraded / unusable"),
    ("e2e", HERE / "test_e2e_workflow.py", "Mixed corpus -> both query paths work"),
    ("docs", HERE / "check_doc_links.py", "Doc links and stale references"),
    ("structure", HERE / "check_structure.py", "Stages, manuals, field names, zh sync"),
]


def main() -> int:
    failures = 0
    for name, path, blurb in SUITES:
        print("=" * 72)
        print(f"  {name}: {blurb}")
        print("=" * 72)
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        print(proc.stdout, end="")
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr, end="")
        if proc.returncode != 0:
            failures += 1
            print(f">>> suite '{name}' FAILED (exit {proc.returncode})")
        print()

    print("=" * 72)
    if failures:
        print(f"RESULT: {failures} of {len(SUITES)} suites failed")
    else:
        print(f"RESULT: all {len(SUITES)} suites passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
