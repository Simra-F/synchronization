"""Agent 2: a single-agent open-weight LLM (e.g. Qwen) that keeps modifying the repository while the MAS works.
The scenario decides WHEN and WHAT KIND of change happens; the LLM decides the specifics (which symbol, the new
name, the new site title/colour, the commit message). Invalid LLM output falls back to a rule-based choice.

Truth is the modifier's exact record of what it did; the tracker grades the MAS against it."""
from __future__ import annotations
import html
import json
import random
import re
import threading

from ctxdrift.symbols import extract, identifiers, rename, valid_new_name

STYLESHEET = "styles/main.css"

SYNONYMS = {"load": "fetch", "fetch": "read", "read": "get", "get": "load", "save": "persist", "persist": "store",
            "store": "write", "write": "save", "add": "create", "create": "insert", "insert": "add",
            "toggle": "flip", "flip": "switch", "switch": "toggle", "render": "draw", "draw": "paint",
            "paint": "render", "handle": "on", "on": "handle", "mount": "build", "build": "setup", "setup": "mount"}
TITLES = ["Taskbox", "Taskbox Pro", "Todo Hub", "Daily Tasks", "Task Garden", "Listicle"]
TAGLINES = ["Get things done.", "Your tasks, nothing else.", "Small list, big days.", "Plan less, do more."]
COLORS = ["#2f6fde", "#c2410c", "#15803d", "#7c3aed", "#be123c", "#0f766e"]

PROMPT = """You are a developer working on the Taskbox web app repository, pushing a change straight to main.
Change type: {op}
Current top-level JavaScript symbols: {symbols}
You are: {author}
Reply with ONLY a JSON object:
- for "rename": {{"symbol": "<one of the symbols above>", "new_name": "<new camelCase or UPPER_CASE identifier>", "message": "<commit message>"}}
- for "cosmetic": {{"title": "<new site title, max 3 words>", "tagline": "<new tagline, max 8 words>", "accent": "#rrggbb", "message": "<commit message>"}}"""


class Truth:
    """Ground truth: current name of every original symbol, names that are no longer (or never were) on main,
    and the requirement issues that are open."""

    def __init__(self, symbols, base_facts):
        self.current = {s: s for s in symbols}      # original name -> current name on main
        self.history = set(symbols)                 # every name a tracked symbol has ever had
        self.proposed = set()                       # names that only exist on unmerged/rejected PR branches
        self.requirements = {}                      # open issue number -> Requirement
        self.base_facts = list(base_facts)
        self.extra_facts = []                       # rules that arrived with contributor-added helpers
        self.lock = threading.RLock()

    def add_symbol(self, name, fact=None):
        with self.lock:
            self.current[name] = name
            self.history.add(name)
            if fact:
                self.extra_facts.append(fact)

    def rename(self, old, new):
        with self.lock:
            for sid, name in self.current.items():
                if name == old:
                    self.current[sid] = new
            self.history.add(new)
            self.proposed.discard(new)

    def snapshot(self) -> dict:
        with self.lock:
            names = set(self.current.values())
            facts = {f.name: f for f in self.base_facts + self.extra_facts}
            for req in self.requirements.values():
                facts[req.fact.name] = req.fact
            return {"current": names, "stale": (self.history | self.proposed) - names,
                    "facts": [f.resolve(self.current) for f in facts.values()]}


class Modifier:
    def __init__(self, repo, truth, level, requirements, llm=None, seed=0, clock=None, helpers=(), contributors=("maintainer-bot",)):
        self.repo, self.truth, self.level, self.reqs, self.llm = repo, truth, level, requirements, llm
        self.helpers, self.contributors = list(helpers), list(contributors)
        self.author = self.contributors[0]
        self.rng = random.Random(seed)
        self.clock = clock
        self.log: list[dict] = []
        self.renames: list[tuple] = []      # (old, new) on main, most recent last (for reverts)
        self.open_prs: dict = {}            # pr number -> (old, new)
        self._stop = threading.Event()
        self._thread = None

    # --- scheduling ---------------------------------------------------------------------------
    def start(self):
        """Run in parallel with the MAS: wait an exponential time (mean = one MAS stage / rate), act, repeat."""
        if self.level.rate > 0:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()

    def _loop(self):
        while not self._stop.wait(self.rng.expovariate(self.level.rate / self.clock.hop_seconds)):
            self.act()

    def step(self):
        """Lockstep mode (reproducible): act Poisson(rate) times, called by the MAS before each stage."""
        k, t = 0, self.rng.expovariate(1.0) if self.level.rate > 0 else 2.0
        while self.level.rate > 0 and t < self.level.rate:
            k += 1
            t += self.rng.expovariate(1.0)
        for _ in range(k):
            self.act()

    def act(self):
        if len(self.log) >= self.level.max_events:
            return None
        ops = {op: w for op, w in self.level.weights.items() if self._feasible(op)}
        if not ops:
            return None
        op = self.rng.choices(list(ops), weights=list(ops.values()))[0]
        self.author = self.rng.choice(self.contributors)      # many people contribute to an open-source repo
        with self.truth.lock:                     # repo write + truth update are one atomic step
            try:
                rec = getattr(self, "_" + op)()
            except Exception as e:                # a failed API call must not kill the thread
                rec = {"op": op, "error": str(e)[:200]}
        rec.update(op=op, author=self.author, repo_version=self.repo.version)
        self.log.append(rec)
        print(f"[modifier] {rec.get('summary', rec)}")
        return rec

    def _feasible(self, op):
        return {"issue": len(self.truth.requirements) < len(self.reqs),
                "close_issue": bool(self.truth.requirements),
                "revert": bool(self._revertable()),
                "pr_merge": bool(self.open_prs), "pr_reject": bool(self.open_prs),
                "add_helper": bool(self._missing_helpers())}.get(op, True)

    # --- LLM ----------------------------------------------------------------------------------
    def _ask(self, op, symbols):
        if not self.llm or self.llm.backend in ("mock", "sim"):
            return {}
        try:
            text, _ = self.llm.chat("You output only JSON.", PROMPT.format(op=op, symbols=", ".join(symbols), author=self.author),
                                    agent="modifier")
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception:
            return {}

    def _pick_rename(self):
        files = self.repo.files()
        symbols = sorted(set(extract(files)) & set(self.truth.current.values()))
        taken = self.truth.history | self.truth.proposed | identifiers(files)   # never collide with any name in the code
        p = self._ask("rename", symbols)
        old, new = p.get("symbol"), p.get("new_name")
        if old not in symbols or not valid_new_name(new, taken):
            old = self.rng.choice(symbols)
            new, source = self._fallback_name(old, taken), "rule"
        else:
            source = "llm"
        msg = p.get("message") if source == "llm" and isinstance(p.get("message"), str) else f"refactor: rename {old} to {new}"
        return files, old, new, msg[:100], source

    def _fallback_name(self, old, taken):
        m = re.match(r"([a-z]+)(.*)", old)
        if m and m.group(1) in SYNONYMS:
            cand = SYNONYMS[m.group(1)] + m.group(2)
        elif old.isupper():
            cand = self.rng.choice(["APP_", "TASKBOX_", "UI_"]) + old
        else:
            cand = "new" + old[0].upper() + old[1:]
        while cand in taken:
            cand += "2"
        return cand

    # --- operations ---------------------------------------------------------------------------
    def _commit(self, message, changes, files):
        """Every commit also updates the site footer, so each change is visible on the website."""
        page = changes.get("index.html", files.get("index.html", ""))
        footer = html.escape(f"{message} (by {self.author})")
        changes["index.html"] = re.sub(r'(<span id="last-change">).*?(</span>)', lambda m: m.group(1) + footer + m.group(2), page)
        self.repo.commit(message, changes, author=self.author)

    def _rename(self):
        files, old, new, msg, source = self._pick_rename()
        self._commit(msg, rename(files, old, new), files)
        self.truth.rename(old, new)
        self.renames.append((old, new))
        return {"old": old, "new": new, "source": source, "summary": f"commit  {msg}"}

    def _cosmetic(self):
        files = self.repo.files()
        p = self._ask("cosmetic", [])
        title = p.get("title") if isinstance(p.get("title"), str) else self.rng.choice(TITLES)
        tagline = p.get("tagline") if isinstance(p.get("tagline"), str) else self.rng.choice(TAGLINES)
        accent = p.get("accent") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(p.get("accent"))) else self.rng.choice(COLORS)
        title, tagline = html.escape(title[:40]), html.escape(tagline[:80])
        page = files.get("index.html", "")
        page = re.sub(r'(<h1 id="site-title">).*?(</h1>)', lambda m: m.group(1) + title + m.group(2), page)
        page = re.sub(r"(<title>).*?(</title>)", lambda m: m.group(1) + title + m.group(2), page)
        page = re.sub(r'(<p id="tagline">).*?(</p>)', lambda m: m.group(1) + tagline + m.group(2), page)
        css = re.sub(r"(--accent:\s*)#[0-9a-fA-F]{3,6}", lambda m: m.group(1) + accent, files.get(STYLESHEET, ""))
        msg = p.get("message") if isinstance(p.get("message"), str) else f"style: retitle site to {title}"
        self._commit(msg[:100], {"index.html": page, STYLESHEET: css}, files)
        return {"title": title, "accent": accent, "source": "llm" if p else "rule", "summary": f"commit  {msg[:100]}"}

    def _issue(self):
        open_titles = {r.title for r in self.truth.requirements.values()}
        req = self.rng.choice([r for r in self.reqs if r.title not in open_titles])
        n = self.repo.open_issue(req.title, req.body, author=self.author)
        self.truth.requirements[n] = req
        return {"number": n, "summary": f"issue   #{n} opened: {req.title}"}

    def _close_issue(self):
        n = self.rng.choice(sorted(self.truth.requirements))
        self.repo.close_issue(n)
        req = self.truth.requirements.pop(n)
        return {"number": n, "summary": f"issue   #{n} closed (withdrawn): {req.title}"}

    def _revertable(self):
        names = set(self.truth.current.values())
        return [(o, n) for o, n in self.renames if n in names and o not in names]

    def _revert(self):
        old, new = self._revertable()[-1]
        files = self.repo.files()
        msg = f"revert: rename {old} to {new}"
        self._commit(msg, rename(files, new, old), files)
        self.truth.rename(new, old)
        self.renames.remove((old, new))
        return {"old": new, "new": old, "summary": f"commit  {msg}"}

    def _pr_open(self):
        files, old, new, msg, source = self._pick_rename()
        n = self.repo.open_pr(msg, f"Proposal: rename `{old}` to `{new}`.", rename(files, old, new), author=self.author)
        self.open_prs[n] = (old, new)
        self.truth.proposed.add(new)
        return {"number": n, "old": old, "new": new, "source": source, "summary": f"PR      #{n} opened: {msg}"}

    def _pr_merge(self):
        n = self.rng.choice(sorted(self.open_prs))
        old, new = self.open_prs.pop(n)
        if old in self.truth.current.values() and self.repo.merge_pr(n):
            self.truth.rename(old, new)
            self.renames.append((old, new))
            return {"number": n, "merged": True, "summary": f"PR      #{n} merged: {old} -> {new}"}
        self.repo.close_pr(n)
        return {"number": n, "merged": False, "summary": f"PR      #{n} conflicted, closed"}

    def _pr_reject(self):
        n = self.rng.choice(sorted(self.open_prs))
        self.open_prs.pop(n)
        self.repo.close_pr(n)
        return {"number": n, "summary": f"PR      #{n} rejected"}


    def _missing_helpers(self):
        files = self.repo.files()
        return [h for h in self.helpers if h.path not in files]

    def _add_helper(self):
        """A contributor adds a shared module (and maybe a rule that new code must use it)."""
        h = self.rng.choice(self._missing_helpers())
        files = self.repo.files()
        tag = f'  <script src="{h.path}"></script>\n'
        page = files.get("index.html", "")
        page = page.replace('  <script src="src/storage.js">', tag + '  <script src="src/storage.js">', 1)
        changes = {h.path: h.content, "index.html": page}
        if h.rule:
            changes["CONTRIBUTING.md"] = files["CONTRIBUTING.md"].replace("\n\n## Workflow", f"\n{h.rule}\n\n## Workflow", 1)
        msg = f"feat: add {h.symbol}() helper in {h.path}"
        self._commit(msg, changes, files)
        self.truth.add_symbol(h.symbol, h.fact)
        return {"path": h.path, "summary": f"commit  {msg}"}


class HopClock:
    """Running average of how long one MAS stage takes; scales the modifier's waiting times."""

    def __init__(self, hop_seconds=20.0):
        self.hop_seconds = hop_seconds
        self._n = 0

    def update(self, seconds):
        self._n += 1
        seconds = max(seconds, 0.01)
        self.hop_seconds = seconds if self._n == 1 else 0.7 * self.hop_seconds + 0.3 * seconds
