"""Run the modifier on its own against a real GitHub repo and watch the site change.

  python -m github_modifier --level heavy --minutes 5 --interval 20 --reset      # default repo: $GITHUB_REPO
"""
from __future__ import annotations
import argparse
import os
import time

from ctxdrift.llm import LLM
from ctxdrift.symbols import extract
from ctxdrift.task import CONTRIBUTORS, FACTS, HELPERS, REQUIREMENTS, load_seed

from .github_repo import GitHubRepo
from .modifier import HopClock, Modifier, Truth
from .scenarios import LEVELS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPO", "Simra-F/sample_repository"),
                    help="OWNER/NAME of the sandbox repo (default: $GITHUB_REPO or Simra-F/sample_repository)")
    ap.add_argument("--level", choices=list(LEVELS), default="moderate")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--interval", type=float, default=20, help="seconds that count as one 'stage' for the level's rate")
    ap.add_argument("--reset", action="store_true", help="restore the seed files on main first")
    ap.add_argument("--backend", choices=["ollama", "openai", "sim", "mock"])
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    seed = load_seed()
    repo = GitHubRepo(a.repo)
    if a.reset:
        repo.reset(seed)
        print("Reset main to the seed state.")
    files = repo.files()
    level = LEVELS[a.level]
    level.max_events = 10_000
    m = Modifier(repo, Truth(extract(files), FACTS), level, REQUIREMENTS,
                 LLM(a.model, a.backend, a.base_url, max_tokens=300), a.seed, HopClock(a.interval),
                 helpers=HELPERS, contributors=CONTRIBUTORS)
    print(f"Modifying {repo.url} ({a.level}) for {a.minutes} min. Site: {repo.pages_url}")
    m.start()
    try:
        time.sleep(a.minutes * 60)
    except KeyboardInterrupt:
        pass
    m.stop()
    print(f"Done: {len(m.log)} changes.")


if __name__ == "__main__":
    main()
