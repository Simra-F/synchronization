"""Repository interface shared by the in-memory repo (fast experiments) and the real GitHub repo
(github_modifier/github_repo.py). Every write emits an event to subscribers: that is the "webhook channel"
the source of truth V listens on. `version` = number of events so far."""
from __future__ import annotations
import os
import subprocess
import tempfile
import threading
import time

MARKER = "<!-- ctxdrift -->"
TEXT_EXT = (".html", ".js", ".css", ".md", ".json", ".txt", ".yml", ".yaml", ".svg")
TEXT_NAMES = ("LICENSE", ".editorconfig", ".gitignore", ".nojekyll")


def is_text(path: str) -> bool:
    return path.endswith(TEXT_EXT) or os.path.basename(path) in TEXT_NAMES


def clean(body) -> str:
    return (body or "").replace(MARKER, "").replace("\r\n", "\n").strip()


def three_way(base, ours, theirs):
    """Merge one file like git does. Returns (merged_text, ok). None = file absent."""
    if ours == theirs or theirs == base:
        return ours, True
    if ours == base:
        return theirs, True
    if base is None or ours is None or theirs is None:
        return None, False                       # add/add or delete/modify conflict
    try:
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
                paths.append(os.path.join(d, name))
                with open(paths[-1], "w") as f:
                    f.write(text)
            r = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True, text=True)
            return r.stdout, r.returncode == 0
    except OSError:                              # no git: only non-overlapping whole-file changes merge
        return None, False


class BaseRepo:
    def __init__(self):
        self.events: list[dict] = []
        self._subs = []
        self.lock = threading.RLock()

    @property
    def version(self) -> int:
        return len(self.events)

    def subscribe(self, fn):
        self._subs.append(fn)

    def _emit(self, **event):
        with self.lock:
            event.update(version=len(self.events) + 1, time=time.time())
            self.events.append(event)
            subs = list(self._subs)
        for fn in subs:
            fn(event)

    # implemented by subclasses: files(), issues(), prs(), checkpoint(), commit(), open_issue(), close_issue(),
    # open_pr(), merge_pr(), close_pr()

    def digest(self, max_chars: int = 12000, focus=()) -> str:
        """The text view of the repo an agent gets: metadata, then file contents with focus files first
        (a real agent retrieves the files relevant to its task; everything else only if the budget allows)."""
        files = self.files()
        issues = [i for i in self.issues() if i["state"] == "open"]
        prs = [p for p in self.prs() if p["state"] == "open"]
        commits = [e for e in self.events if e["kind"] == "commit"][-5:]
        text = "FILES: " + ", ".join(sorted(files))
        text += "\nRECENT COMMITS:\n" + ("\n".join(f"  - {c['message']} ({c.get('author', '?')})" for c in reversed(commits)) or "  (none)")
        text += "\nOPEN ISSUES:\n" + ("\n".join(f"  #{i['number']} {i['title']}: {clean(i['body'])}" for i in issues) or "  (none)")
        text += "\nOPEN PULL REQUESTS (not merged):\n" + ("\n".join(f"  #{p['number']} {p['title']}" for p in prs) or "  (none)")
        order = [p for p in focus if p in files] + sorted(p for p in files if p not in focus)
        for path in order:
            if not path.endswith((".js", ".html", ".css", ".md", ".json")) or path.startswith(".github/"):
                continue
            block = f"\n\n--- {path} ---\n{files[path]}"
            if len(text) + len(block) > max_chars:
                text += f"\n\n--- {path} --- (not shown)"
                continue
            text += block
        return text


class LocalRepo(BaseRepo):
    """In-memory stand-in for GitHub with the same API (issues and PRs share one number sequence)."""

    def __init__(self, files: dict):
        super().__init__()
        self._files = dict(files)
        self._issues: list[dict] = []
        self._prs: list[dict] = []

    def files(self):
        with self.lock:
            return dict(self._files)

    def checkpoint(self):
        """What a PR is based on (GitHub: a commit sha; here: a copy of the files)."""
        return self.files()

    def issues(self):
        with self.lock:
            return [dict(i) for i in self._issues]

    def prs(self):
        with self.lock:
            return [{"number": p["number"], "title": p["title"], "state": p["state"]} for p in self._prs]

    def _next_number(self):
        return len(self._issues) + len(self._prs) + 1

    def _apply(self, changes):
        for path, content in changes.items():
            if content is None:
                self._files.pop(path, None)
            else:
                self._files[path] = content

    def commit(self, message, changes, author="maintainer-bot"):
        with self.lock:
            self._apply(changes)
        self._emit(kind="commit", message=message, changes=dict(changes), author=author)

    def open_issue(self, title, body, author="maintainer-bot"):
        with self.lock:
            n = self._next_number()
            self._issues.append({"number": n, "title": title, "body": body, "state": "open"})
        self._emit(kind="issue_open", number=n, title=title, body=body, author=author)
        return n

    def close_issue(self, number):
        with self.lock:
            issue = next(i for i in self._issues if i["number"] == number)
            issue["state"] = "closed"
        self._emit(kind="issue_close", number=number, title=issue["title"], body=issue["body"])

    def open_pr(self, title, body, changes, author="maintainer-bot", base=None):
        with self.lock:
            n = self._next_number()
            base_files = base if base is not None else self._files
            self._prs.append({"number": n, "title": title, "body": body, "state": "open", "changes": dict(changes),
                              "base": {p: base_files.get(p) for p in changes}})
        self._emit(kind="pr_open", number=n, title=title, author=author)
        return n

    def merge_pr(self, number) -> bool:
        """Three-way merge of every file (base = what the PR was written against). False on any conflict."""
        with self.lock:
            pr = next(p for p in self._prs if p["number"] == number)
            if pr["state"] != "open":
                return False
            merged = {}
            for path, ours in pr["changes"].items():
                text, ok = three_way(pr["base"][path], ours, self._files.get(path))
                if not ok:
                    return False
                merged[path] = text
            self._apply(merged)
            pr["state"] = "merged"
        self._emit(kind="pr_merge", number=number, title=pr["title"], changes=merged)
        return True

    def close_pr(self, number):
        with self.lock:
            pr = next(p for p in self._prs if p["number"] == number)
            pr["state"] = "closed"
        self._emit(kind="pr_close", number=number, title=pr["title"])
