"""The real GitHub repository, behind the same interface as ctxdrift.repo.LocalRepo.
Writes go through the Git Data API (one atomic commit per change), so GitHub Pages redeploys on every commit.
Token: $GITHUB_TOKEN, else `gh auth token`."""
from __future__ import annotations
import base64
import os
import subprocess
import uuid

import requests

from ctxdrift.repo import MARKER, BaseRepo, clean, is_text


def _gh_token():
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("No GitHub token: set GITHUB_TOKEN or run `gh auth login`.")


class GitHubRepo(BaseRepo):
    def __init__(self, full_name: str, branch: str = "main", token: str | None = None):
        super().__init__()
        self.full_name, self.branch = full_name, branch
        self.api = f"https://api.github.com/repos/{full_name}"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token or os.getenv('GITHUB_TOKEN') or _gh_token()}",
                               "Accept": "application/vnd.github+json"})
        self._blobs: dict = {}   # blob sha -> text (so re-reading the repo only downloads changed files)

    @property
    def url(self):
        return f"https://github.com/{self.full_name}"

    @property
    def pages_url(self):
        owner, name = self.full_name.split("/")
        return f"https://{owner.lower()}.github.io/{name}/"

    def _req(self, method, path, ok=(200, 201, 204), **kw):
        r = self.s.request(method, self.api + path, timeout=60, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"GitHub {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # --- reads --------------------------------------------------------------------------------
    def _head(self, branch):
        return self._req("GET", f"/git/ref/heads/{branch}")["object"]["sha"]

    def files(self):
        tree = self._req("GET", f"/git/trees/{self.branch}", params={"recursive": "1"})["tree"]
        out = {}
        for t in tree:
            if t["type"] != "blob" or not is_text(t["path"]):
                continue
            if t["sha"] not in self._blobs:
                b = self._req("GET", f"/git/blobs/{t['sha']}")
                self._blobs[t["sha"]] = base64.b64decode(b["content"]).decode("utf-8", "replace")
            out[t["path"]] = self._blobs[t["sha"]]
        return out

    def issues(self):
        items = self._req("GET", "/issues", params={"state": "all", "per_page": 100})
        return [{"number": i["number"], "title": i["title"], "body": clean(i.get("body")), "state": i["state"]}
                for i in items if "pull_request" not in i]

    def prs(self):
        items = self._req("GET", "/pulls", params={"state": "all", "per_page": 100})
        return [{"number": p["number"], "title": p["title"], "state": "merged" if p.get("merged_at") else p["state"]}
                for p in items]

    # --- writes -------------------------------------------------------------------------------
    def checkpoint(self):
        """The commit a PR is based on."""
        return self._head(self.branch)

    def _commit(self, branch, message, changes, base=True, author=None, parent=None):
        head = parent or self._head(branch)
        entries = [{"path": p, "mode": "100644", "type": "blob", **({"sha": None} if c is None else {"content": c})}
                   for p, c in changes.items()]
        body = {"tree": entries}
        if base:
            body["base_tree"] = self._req("GET", f"/git/commits/{head}")["tree"]["sha"]
        tree = self._req("POST", "/git/trees", json=body)["sha"]
        body = {"message": message, "tree": tree, "parents": [head]}
        if author:   # contributor persona as the commit author (example.com: reserved, reaches nobody)
            body["author"] = {"name": author, "email": f"{author}@users.noreply.example.com"}
        sha = self._req("POST", "/git/commits", json=body)["sha"]
        self._req("PATCH", f"/git/refs/heads/{branch}", json={"sha": sha})
        return sha

    def commit(self, message, changes, author="maintainer-bot"):
        with self.lock:
            sha = self._commit(self.branch, message, changes, author=author)
        self._emit(kind="commit", message=message, changes=dict(changes), author=author, sha=sha[:7])

    def open_issue(self, title, body, author="maintainer-bot"):
        j = self._req("POST", "/issues", json={"title": title, "body": f"{body}\n\n(by {author})\n{MARKER}"})
        self._emit(kind="issue_open", number=j["number"], title=title, body=clean(j["body"]), author=author)
        return j["number"]

    def close_issue(self, number):
        j = self._req("PATCH", f"/issues/{number}", json={"state": "closed"})
        self._emit(kind="issue_close", number=number, title=j["title"], body=clean(j.get("body")))

    def open_pr(self, title, body, changes, author="maintainer-bot", base=None):
        """base = the commit the changes were written against (so GitHub does a real three-way merge)."""
        branch = f"ctxdrift/{uuid.uuid4().hex[:8]}"
        with self.lock:
            start = base or self._head(self.branch)
            self._req("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": start})
            self._commit(branch, title, changes, author=author)
        j = self._req("POST", "/pulls", json={"title": title, "head": branch, "base": self.branch,
                                              "body": f"{body}\n\n(by {author})\n{MARKER}"})
        self._emit(kind="pr_open", number=j["number"], title=title, author=author, url=j["html_url"])
        return j["number"]

    def merge_pr(self, number) -> bool:
        with self.lock:
            r = self.s.put(f"{self.api}/pulls/{number}/merge", json={"merge_method": "squash"}, timeout=60)
        if r.status_code != 200:
            return False            # merge conflict / not mergeable
        title = self._req("GET", f"/pulls/{number}")["title"]
        self._emit(kind="pr_merge", number=number, title=title, changes=None)   # V re-reads the repo
        return True

    def close_pr(self, number):
        j = self._req("PATCH", f"/pulls/{number}", json={"state": "closed"})
        self._emit(kind="pr_close", number=number, title=j["title"])

    def reset(self, seed_files: dict):
        """Restore main to the seed files with one normal (non-force) commit, close everything this tool opened
        and delete its branches. Run before each experiment run so every run starts from the same state."""
        with self.lock:
            self._commit(self.branch, "reset: restore Taskbox seed state (ctxdrift)", seed_files, base=False)
        for item in self._req("GET", "/issues", params={"state": "open", "per_page": 100}):
            if MARKER in (item.get("body") or ""):
                self._req("PATCH", f"/issues/{item['number']}", json={"state": "closed"})
        for pr in self._req("GET", "/pulls", params={"state": "all", "per_page": 100}):
            ref = pr["head"]["ref"]
            if ref.startswith("ctxdrift/"):
                self._req("DELETE", f"/git/refs/heads/{ref}", ok=(204, 404, 422))
        self.events.clear()
