"""Top-level JavaScript symbols (functions, consts, classes) and repo-wide renames."""
from __future__ import annotations
import re

TOP_LEVEL = re.compile(r"^(?:async\s+)?(?:function\s*\*?\s*([A-Za-z_$][\w$]*)|(?:const|let|var|class)\s+([A-Za-z_$][\w$]*))", re.M)
IDENT = re.compile(r"^[A-Za-z_$][\w$]{2,30}$")
RESERVED = set("""break case catch class const continue debugger default delete do else export extends finally for function
if import in instanceof let new return super switch this throw try typeof var void while with yield async await null true
false undefined document window localStorage module require console""".split())


def extract(files: dict) -> dict:
    """{symbol: path} for top-level symbols in .js files outside tests/."""
    out = {}
    for path, text in files.items():
        if path.endswith(".js") and not path.startswith("tests/") and text:
            for m in TOP_LEVEL.finditer(text):
                out[m.group(1) or m.group(2)] = path
    return out


def rename(files: dict, old: str, new: str) -> dict:
    """{path: new_content} for every file that mentions `old` as a whole word."""
    pat = re.compile(rf"(?<![\w$]){re.escape(old)}(?![\w$])")
    return {p: pat.sub(new, t) for p, t in files.items() if t and pat.search(t)}


def mentions(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text) is not None


def valid_new_name(name, taken) -> bool:
    return bool(isinstance(name, str) and IDENT.match(name) and name not in RESERVED and name not in taken)


def identifiers(files: dict) -> set:
    """Every identifier-looking token in the JS files (used to avoid rename collisions with locals)."""
    return {t for p, text in files.items() if p.endswith(".js") for t in re.findall(r"[A-Za-z_$][\w$]*", text or "")}
