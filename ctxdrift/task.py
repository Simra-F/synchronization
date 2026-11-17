"""Feature A for the sample repository, the facts the tracker checks, the requirement changes contributors can file
as issues, and helpers contributors can add. <<name>> means "the CURRENT name of the symbol originally called name"."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from .repo import is_text
from .tracker import Fact, Requirement

SEED_DIR = Path(__file__).resolve().parents[1] / "sample_repository"

TASK = ("Feature A: add an Export button to the Taskbox page that downloads all tasks as a CSV file "
        "(one row per task: id, title, done). Read tasks through the storage API in src/storage.js, register the "
        "button in BUTTONS in src/components/toolbar.js, use no external libraries, and add a node:test test in a new "
        "file tests/export.test.js that require()s the conversion function (a pure, exported function with no DOM "
        "code). Keep every existing function and test.")

# Files at the core of the MAS's task: shown first in the repo view, and changes to them are always relevant.
FOCUS_FILES = ["src/storage.js", "src/components/toolbar.js", "index.html", "CONTRIBUTING.md", "src/app.js",
               "tests/storage.test.js", "docs/storage-api.md",
               "src/utils/format.js"]

# Words that make an issue (or a newly added file/symbol) relevant to this task.
KEYWORDS = ["export", "csv", "json", "download", "button"]

# Simulated open-source contributors (fictional handles).
CONTRIBUTORS = ["alice-dev", "bkumar", "chen-oss", "dana-maint", "eli-contrib", "deps-bot"]

FACTS = [
    Fact("reads_via_storage", "Reads tasks through the storage API's load function", any_of=[r"\b<<loadTasks>>\b"]),
    Fact("registers_button", "Registers the button in the toolbar registry", any_of=[r"\b<<BUTTONS>>\b"]),
    Fact("no_external_libs", "No external libraries or CDNs",
         none_of=[r"<script[^>]*src=[\"']https?:", r"\brequire\(\s*['\"](?!node:|\.)", r"cdn\.|unpkg|jsdelivr"]),
    Fact("has_test", "Includes a node:test test", any_of=[r"node:test", r"\btest\("]),
    Fact("export_format", "Export writes CSV", any_of=[r"\bcsv\b"]),
]

REQUIREMENTS = [
    Requirement("Export must write JSON Lines, not CSV",
                "Downstream tools read JSON Lines. The export must write one JSON object per line (.jsonl). CSV is no longer wanted.",
                Fact("export_format", "Export writes JSON Lines (issue replaced CSV)",
                     any_of=[r"jsonl|json lines|ndjson"], none_of=[r"text/csv", r"\.csv\b"])),
    Requirement("Name the exported file taskbox-export",
                "Exported files must be named taskbox-export with the right extension so support can find them.",
                Fact("export_filename", "Export file is named taskbox-export", any_of=[r"taskbox-export"])),
    Requirement("Label the export button \"Download\"",
                "UX review: the toolbar label for exporting must be \"Download\", not \"Export\".",
                Fact("button_label", "Button is labelled Download", any_of=[r"[\"']Download[\"']"])),
]


@dataclass
class Helper:
    """A module a contributor adds mid-task. If it comes with a rule, the rule becomes a required fact."""
    path: str
    symbol: str
    content: str
    rule: str = ""
    fact: Fact = None


HELPERS = [
    Helper("src/utils/download.js", "downloadFile",
           "// Shared helper: every file download in Taskbox goes through this.\n"
           "function downloadFile(filename, content, mimeType) {\n"
           "  const blob = new Blob([content], { type: mimeType || \"text/plain\" });\n"
           "  const link = document.createElement(\"a\");\n"
           "  link.href = URL.createObjectURL(blob);\n"
           "  link.download = filename;\n"
           "  link.click();\n"
           "  URL.revokeObjectURL(link.href);\n"
           "}\n",
           "- Every file download must go through downloadFile() in src/utils/download.js.",
           Fact("uses_download_helper", "Downloads go through the shared downloadFile helper", any_of=[r"\b<<downloadFile>>\b"])),
    Helper("src/utils/csv.js", "toCsvRow",
           "// Shared helper for CSV output.\n"
           "function toCsvRow(values) {\n"
           "  return values.map((v) => `\"${String(v).replace(/\"/g, '\"\"')}\"`).join(\",\");\n"
           "}\n\n"
           "if (typeof module !== \"undefined\") {\n  module.exports = { toCsvRow };\n}\n"),
]


def load_seed() -> dict:
    return {str(p.relative_to(SEED_DIR)): p.read_text() for p in sorted(SEED_DIR.rglob("*"))
            if p.is_file() and is_text(p.name) and ".git" not in p.parts}
