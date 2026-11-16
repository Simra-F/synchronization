"""Source of truth V (Section 5 of the report).

1. A versioned record of repository facts: top-level symbols (name -> file), contributing rules, issues
   (title, body, state) and PRs (title, state). One snapshot per version.
2. Updated incrementally on every repository event (the repo's subscriber list is the webhook channel): only the
   files a commit touched are re-parsed.
3. Staleness of an agent = number of RELEVANT fact changes between the version it last synced to and now.
   Relevant, in priority order: (0) issues touching the task, (1) symbols in the MAS's focus files (task files +
   files the agents edited), (2) symbols an agent has named, (3) new files/symbols whose name matches the task.
   Changes are net diffs between snapshots, so a rename that was later reverted, or an issue opened and then
   closed before the agent synced, counts as zero (state awareness / reconciliation). Cosmetic commits change
   no facts, so they never trigger an update.
4. `lookup()` lets agents query the current facts themselves (exposed as a tool).
5. `reconcile()` re-reads the whole repo periodically and catches anything the event stream missed
   (e.g. someone editing on github.com).
"""
from __future__ import annotations
import re
import threading
import time
from dataclasses import dataclass

from .repo import clean
from .symbols import extract, mentions


RULES_FILE = "CONTRIBUTING.md"


def rule_facts(path, text) -> dict:
    if path != RULES_FILE or not text:
        return {}
    return {f"rule:{line[2:].strip()}": True for line in text.splitlines() if line.startswith("- ")}


@dataclass
class Change:
    kind: str           # rename | symbol_removed | symbol_added | issue_opened | issue_closed | pr
    text: str           # how the change is told to an agent
    names: tuple = ()   # symbols involved (relevance)
    about: str = ""     # issue/PR text, or the file path for symbol changes
    tier: int = 9       # set by relevant(): lower = more important


class SourceOfTruth:
    def __init__(self, repo, keywords=(), clock_fn=time.time):
        self.repo, self.clock_fn = repo, clock_fn
        self.keywords = [k.lower() for k in keywords]
        self.lock = threading.RLock()
        self.renamed: dict = {}         # old name -> new name, as observed
        self.missed = 0                 # reconciliations that found something the event stream missed
        self.snapshots = [self._read_all()]
        self.times = [clock_fn()]       # when each version appeared (for the time-based staleness bound)
        repo.subscribe(self.on_event)

    @property
    def version(self) -> int:
        return len(self.snapshots) - 1

    @property
    def facts(self) -> dict:
        return self.snapshots[-1]

    # --- keeping V current --------------------------------------------------------------------
    def _read_all(self) -> dict:
        files = self.repo.files()
        facts = {f"symbol:{n}": p for n, p in extract(files).items()}
        facts.update(rule_facts(RULES_FILE, files.get(RULES_FILE)))
        for i in self.repo.issues():
            facts[f"issue:{i['number']}"] = (i["title"], clean(i["body"]), i["state"])
        for p in self.repo.prs():
            facts[f"pr:{p['number']}"] = (p["title"], p["state"])
        return facts

    def on_event(self, e: dict):
        with self.lock:
            facts = dict(self.facts)
            kind = e["kind"]
            if kind in ("commit", "pr_merge"):
                if e.get("changes") is None:          # contents unknown (GitHub merge): re-read everything
                    facts = self._read_all()
                else:                                  # incremental: re-parse only the touched files
                    for path, content in e["changes"].items():
                        facts = {k: v for k, v in facts.items() if not (k.startswith("symbol:") and v == path)}
                        facts.update({f"symbol:{n}": p for n, p in extract({path: content or ""}).items()})
                        if path == RULES_FILE:
                            facts = {k: v for k, v in facts.items() if not k.startswith("rule:")}
                            facts.update(rule_facts(path, content))
            if kind in ("issue_open", "issue_close"):
                facts[f"issue:{e['number']}"] = (e["title"], clean(e["body"]), "open" if kind == "issue_open" else "closed")
            if kind.startswith("pr_"):
                facts[f"pr:{e['number']}"] = (e["title"], {"pr_open": "open", "pr_merge": "merged", "pr_close": "closed"}[kind])
            self._push(facts)

    def reconcile(self):
        full = self._read_all()
        with self.lock:
            if full != self.facts:
                self.missed += 1
                self._push(full)

    def _push(self, facts):
        before = self.facts
        self.snapshots.append(facts)
        self.times.append(self.clock_fn())
        for c in describe(before, facts):
            if c.kind == "rename":
                self.renamed[c.names[0]] = c.names[1]

    # --- what agents get ----------------------------------------------------------------------
    def diff(self, since: int) -> list:
        with self.lock:
            return describe(self.snapshots[since], self.facts)

    def relevant(self, changes, context: str, focus=()) -> list:
        """Changes that matter to the MAS's task, most important first (see module docstring)."""
        out = []
        for c in changes:
            hit = lambda text: any(k in text.lower() for k in self.keywords)
            if c.kind.startswith(("issue", "rule")):
                tier = 0 if hit(c.about) or any(mentions(n, c.about) for n in _names(context)) else None
            elif c.kind == "pr":
                tier = None
            elif c.about in focus:
                tier = 1
            elif any(mentions(n, context) for n in c.names):
                tier = 2
            elif c.kind == "symbol_added" and hit(c.about + " " + " ".join(c.names)):
                tier = 3
            else:
                tier = None
            if tier is not None:
                c.tier = tier
                out.append(c)
        return sorted(out, key=lambda c: c.tier)

    def oldest_pending(self, since: int, context: str, focus=()):
        """Clock time at which the first still-pending relevant change appeared after version `since` (or None)."""
        with self.lock:
            base = self.snapshots[since]
            for k in range(since + 1, len(self.snapshots)):
                if self.relevant(describe(base, self.snapshots[k]), context, focus):
                    return self.times[k] if self.relevant(self.diff(since), context, focus) else None
        return None

    def divergence(self, text: str, since: int, task: str) -> list:
        """Facts in V that an agent's OUTPUT contradicts or misses (drift-triggered sync). Checked by code, no LLM:
          - uses a symbol that was renamed or removed since `since`
          - uses a name that only exists in an unmerged / rejected PR
          - ignores a new task-relevant issue or rule (none of its distinctive words appear)
          - still follows a withdrawn issue or rule (its distinctive words still appear)"""
        task_words = set(re.findall(r"[a-z]{4,}", task.lower()))
        distinct = lambda about: set(re.findall(r"[a-z]{4,}", about.lower())) - task_words
        low = text.lower()
        out = []
        for c in self.diff(since):
            if c.kind in ("rename", "symbol_removed") and mentions(c.names[0], text):
                out.append(c)
            elif c.kind in ("issue_opened", "rule_added") and self.relevant([c], text) and not any(w in low for w in distinct(c.about)):
                out.append(c)
            elif c.kind in ("issue_closed", "rule_removed") and self.relevant([c], text) and any(w in low for w in distinct(c.about)):
                out.append(c)
        symbols = {k[7:] for k in self.facts if k.startswith("symbol:")}
        for key, val in self.facts.items():
            if key.startswith("pr:") and val[1] != "merged":
                for old, new in re.findall(r"rename (\w+) to (\w+)", val[0]):
                    if new not in symbols and mentions(new, text):
                        out.append(Change("pr", f"`{new}` only exists in unmerged PR #{key[3:]}; it is not on main, do not use it.", (new, old)))
        return out

    def brief(self, version: int, files: dict) -> str:
        """Compact, code-generated view of the repository at `version` (no LLM): the files, every top-level
        symbol grouped by file, the contributing rules, open issues and open PRs. Replaces raw file dumps."""
        facts = self.snapshots[version]
        by_file = {}
        for k, path in facts.items():
            if k.startswith("symbol:"):
                by_file.setdefault(path, []).append(k[7:])
        lines = ["FILES: " + ", ".join(sorted(files)), "SYMBOLS (top-level, by file):"]
        lines += [f"  {path}: {', '.join(sorted(names))}" for path, names in sorted(by_file.items())]
        lines.append("RULES (CONTRIBUTING.md):")
        lines += [f"  - {k[5:]}" for k in facts if k.startswith("rule:")]
        lines.append("OPEN ISSUES:")
        lines += [f"  #{k[6:]} {v[0]}: {v[1]}" for k, v in facts.items() if k.startswith("issue:") and v[2] == "open"]
        lines.append("OPEN PULL REQUESTS (not merged):")
        lines += [f"  #{k[3:]} {v[0]}" for k, v in facts.items() if k.startswith("pr:") and v[1] == "open"]
        return "\n".join(lines)

    def lookup(self, name: str) -> str:
        name = str(name).strip().strip("`")
        facts = self.facts
        if f"symbol:{name}" in facts:
            return f"`{name}` exists on main in {facts[f'symbol:{name}']} (repo version {self.version})."
        if name in self.renamed:
            cur, seen = name, set()
            while cur in self.renamed and cur not in seen:
                seen.add(cur)
                cur = self.renamed[cur]
            if f"symbol:{cur}" in facts:
                return f"`{name}` no longer exists; it was renamed to `{cur}` ({facts[f'symbol:{cur}']})."
        m = re.fullmatch(r"#?(\d+)", name)
        if m:
            for kind in ("issue", "pr"):
                if f"{kind}:{m.group(1)}" in facts:
                    v = facts[f"{kind}:{m.group(1)}"]
                    return f"{kind} #{m.group(1)}: {v[0]} [{v[-1]}]" + (f" - {v[1]}" if kind == "issue" else "")
        files = self.repo.files()
        if name in files:
            return files[name][:2000]
        symbols = sorted(k[7:] for k in facts if k.startswith("symbol:"))
        return f"No fact named `{name}`. Current symbols: {', '.join(symbols)}."


def _names(context):
    return set(re.findall(r"\b(?:[a-z]+[A-Z]\w*|[A-Z][A-Z0-9_]{3,})\b", context))


def describe(old: dict, new: dict) -> list:
    changes = []
    removed = {k[7:]: v for k, v in old.items() if k.startswith("symbol:") and k not in new}
    added = {k[7:]: v for k, v in new.items() if k.startswith("symbol:") and k not in old}
    for path in sorted(set(removed.values()) | set(added.values())):
        r = sorted(n for n, p in removed.items() if p == path)
        a = sorted(n for n, p in added.items() if p == path)
        if len(r) == 1 and len(a) == 1:
            changes.append(Change("rename", f"`{r[0]}` was renamed to `{a[0]}` in {path}; `{r[0]}` no longer exists.", (r[0], a[0]), path))
            continue
        changes += [Change("symbol_removed", f"`{n}` was removed from {path}.", (n,), path) for n in r]
        changes += [Change("symbol_added", f"`{n}` was added in {path}.", (n,), path) for n in a]
    renames = [c.names for c in changes if c.kind == "rename"]

    def norm(rule):                     # a rule edited only by a rename is not a new rule
        for a, b in renames:
            rule = re.sub(rf"(?<![\w$]){re.escape(a)}(?![\w$])", b, rule)
        return rule
    old_rules = {norm(k[5:]) for k in old if k.startswith("rule:")}
    new_rules = {k[5:] for k in new if k.startswith("rule:")}
    changes += [Change("rule_added", f"New contributing rule: {r}", about=r) for r in sorted(new_rules - old_rules)]
    changes += [Change("rule_removed", f"Contributing rule removed: {r}", about=r) for r in sorted(old_rules - new_rules)]
    for key in sorted(set(old) | set(new)):
        if key.startswith("rule:"):
            continue
        o, n = old.get(key), new.get(key)
        if o == n or n is None:
            continue
        num = key.split(":")[1]
        if key.startswith("issue:"):
            was_open, is_open = bool(o and o[2] == "open"), n[2] == "open"
            if is_open and not was_open:
                changes.append(Change("issue_opened", f"Issue #{num} is open: \"{n[0]}\". {n[1]}", about=f"{n[0]} {n[1]}"))
            elif was_open and not is_open:
                changes.append(Change("issue_closed", f"Issue #{num} \"{n[0]}\" was closed: its requirement is withdrawn, ignore it.",
                                      about=f"{n[0]} {n[1]}"))
        elif key.startswith("pr:"):
            changes.append(Change("pr", f"PR #{num} \"{n[0]}\" is {n[1]} (only merged PRs change main).", about=n[0]))
    return changes


def render_updates(task: str, changes: list, v_from: int, v_to: int) -> str:
    """Not a raw diff: restate the task, then the changed facts (anchoring)."""
    lines = "\n".join(f"- {c.text}" for c in changes)
    return (f"### REPOSITORY UPDATES (v{v_from} -> v{v_to})\nTask (restated): {task}\n"
            f"The repository changed after the snapshot above. These facts override the snapshot and any earlier "
            f"stage output:\n{lines}\n\n")
