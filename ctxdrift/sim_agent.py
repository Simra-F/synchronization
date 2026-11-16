"""A behavioural model of an LLM sub-agent, for rough predictions and for choosing s / t_max ranges before spending
GPU time. It is NOT a measurement: every probability below is an assumption you can change, and the presets let you
check whether a conclusion survives different assumptions.

What one simulated call does (reading the same prompt a real agent gets):
  - inherits each name/requirement from the previous stage with p_inherit (handoff loss)
  - reads names from the files it is working on (task + previous-stage files) with p_read * attention
  - drops an inherited name that is nowhere in its repo view with p_verify * attention (re-reading catches staleness)
  - applies each change in a REPOSITORY UPDATES section with p_obey (V's patches; short and near the end, so no
    attention penalty)
  - picks up names proposed in OPEN PULL REQUESTS (not real yet) with p_distract * attention (contamination)
  - restates task constraints with p_task, and hallucinates an external library with p_lib
attention = min(1, ctx_ref / prompt_length): long prompts get read less carefully.
"""
from __future__ import annotations
import hashlib
import random
import re

from .symbols import TOP_LEVEL

PRESETS = {   # rough guesses for ~1-2B, ~4-8B and frontier-size models
    "small": dict(p_inherit=0.85, p_read=0.55, p_verify=0.25, p_obey=0.70, p_distract=0.25, p_task=0.80, p_lib=0.06, ctx_ref=9000),
    "medium": dict(p_inherit=0.90, p_read=0.70, p_verify=0.45, p_obey=0.85, p_distract=0.15, p_task=0.90, p_lib=0.03, ctx_ref=14000),
    "large": dict(p_inherit=0.95, p_read=0.85, p_verify=0.70, p_obey=0.95, p_distract=0.06, p_task=0.95, p_lib=0.01, ctx_ref=24000),
}
NAME = re.compile(r"`([A-Za-z_$][\w$]*)`")
PATH = re.compile(r"\b((?:src|tests|docs)/[\w./-]+\.js|index\.html|CONTRIBUTING\.md)\b")
ISSUE_TOKENS = [("jsonl", r"JSON Lines|\.jsonl"), ("filename", r"taskbox-export"), ("label", r"\"Download\"")]


def _section(text, name):
    m = re.search(rf"^### {name}[^\n]*\n(.*?)(?=^### |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def _file_sections(repo_text):
    return dict(re.findall(r"^--- (\S+) ---\n(.*?)(?=^--- |\Z)", repo_text, re.S | re.M))


def sim_reply(user: str, agent: str, preset: str = "small", seed: str = "") -> str:
    p = PRESETS[preset]
    rng = random.Random(hashlib.sha256(f"{seed}|{agent}|{user}".encode()).hexdigest())
    chance = lambda q: rng.random() < q
    task, repo, updates, prev = (_section(user, n) for n in ("TASK", "REPOSITORY", "REPOSITORY UPDATES", "PREVIOUS STAGE"))
    attention = min(1.0, p["ctx_ref"] / max(1, len(user)))
    files = _file_sections(repo)

    # --- names ---------------------------------------------------------------------------------
    names = {n for n in NAME.findall(prev) if chance(p["p_inherit"])}
    for n in list(names):                                     # verify inherited names against the repo view
        if not re.search(rf"(?<![\w$]){re.escape(n)}(?![\w$])", repo) and chance(p["p_verify"] * attention):
            names.discard(n)
    working = set(PATH.findall(task + prev)) | {"CONTRIBUTING.md"}
    rules = re.search(r"^RULES \(CONTRIBUTING\.md\):\n((?:  - .*\n?)*)", repo, re.M)
    if rules and "CONTRIBUTING.md" not in files:          # a V brief lists the rules instead of the file
        files["CONTRIBUTING.md"] = rules.group(1)
    for path, listed in re.findall(r"^  (\S+\.js): (.+)$", repo, re.M):    # a V brief lists symbols by file
        if path in working:
            for n in listed.split(", "):
                if chance(p["p_read"] * attention):
                    names.add(n)
    for path in working:
        for m in TOP_LEVEL.finditer(files.get(path, "")):
            if chance(p["p_read"] * attention):
                names.add(m.group(1) or m.group(2))
        for n in NAME.findall(files.get(path, "")) + re.findall(r"\b(\w+)\(\)", files.get(path, "")):
            if path == "CONTRIBUTING.md" and chance(p["p_read"] * attention):
                names.add(n)
    for old, new in re.findall(r"`(\w+)` was renamed to `(\w+)`", updates):
        if chance(p["p_obey"]):
            had = old in names
            names.discard(old)
            if had or chance(p["p_read"]):
                names.add(new)
    for new in re.findall(r"`(\w+)` was added in", updates):
        if chance(p["p_obey"] * 0.5):
            names.add(new)
    for new in re.findall(r"must go through (\w+)\(\)", updates):
        if chance(p["p_obey"]):
            names.add(new)
    pr_block = repo.split("OPEN PULL REQUESTS", 1)[1].split("\n---", 1)[0] if "OPEN PULL REQUESTS" in repo else ""
    for old, new in re.findall(r"rename (\w+) to (\w+)", pr_block):
        if chance(p["p_distract"] * attention):
            names.add(new)

    # --- requirements --------------------------------------------------------------------------
    reqs = set()
    for tok, pat in [("csv", r"\bCSV\b"), ("test", r"node:test")] + ISSUE_TOKENS:
        if re.search(pat, prev) and chance(p["p_inherit"]):
            reqs.add(tok)
    for tok in ("csv", "test"):
        if chance(p["p_task"]):
            reqs.add(tok)
    issues_block = repo.split("OPEN ISSUES:", 1)[1].split("OPEN PULL REQUESTS", 1)[0] if "OPEN ISSUES:" in repo else ""
    for tok, pat in ISSUE_TOKENS:
        if re.search(pat, issues_block) and chance(p["p_read"] * attention):
            reqs.add(tok)
        opened = [line for line in updates.splitlines() if "is open:" in line and re.search(pat, line)]
        closed = [line for line in updates.splitlines() if "was closed" in line and re.search(pat, line)]
        if opened and chance(p["p_obey"]):
            reqs.add(tok)
        if closed and chance(p["p_obey"]):
            reqs.discard(tok)
        if tok in reqs and re.search(pat, prev) and not re.search(pat, issues_block + updates):
            if chance(p["p_verify"] * attention):
                reqs.discard(tok)                            # nothing in its repo view supports it any more
    if "jsonl" in reqs and "csv" in reqs and chance(p["p_obey"]):
        reqs.discard("csv")

    # --- answer --------------------------------------------------------------------------------
    lines = [f"- uses `{n}`" for n in sorted(names)]
    if "jsonl" in reqs:
        lines.append("- export format: JSONL (.jsonl)")
    if "csv" in reqs:
        lines.append("- export format: CSV" + (" (also writes tasks.csv)" if "jsonl" in reqs else ""))
    if "filename" in reqs:
        lines.append("- file name: taskbox-export")
    if "label" in reqs:
        lines.append('- button label: "Download"')
    if "test" in reqs:
        lines.append("- tests: node:test in tests/")
    if chance(p["p_lib"]):
        lines.append('- <script src="https://cdn.jsdelivr.net/npm/papaparse"></script>')
    return f"{agent.upper()} OUTPUT\nFACTS:\n" + "\n".join(lines)
