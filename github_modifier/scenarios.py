"""Testing scenarios: how hard the modifier hits the repository while the MAS works.
rate    = expected repository events per MAS stage (the modifier runs in parallel; its wait times are scaled to
          the measured duration of a MAS stage so a scenario means the same thing on a fast or slow machine)
weights = mix of operations
  rename      commit: rename a top-level JS symbol everywhere (breaking)
  cosmetic    commit: change site title / tagline / accent colour (visible on the site, NOT breaking)
  issue       open an issue that changes a requirement of the feature
  close_issue close a requirement issue (the requirement is withdrawn)
  revert      commit: revert an earlier rename (the old name becomes correct again)
  pr_open     open a PR proposing a rename (not on main: the proposed name is NOT real yet)
  pr_merge    merge an open PR (or close it on conflict)
  pr_reject   close an open PR without merging (the proposed name is retracted)
  add_helper  commit: a contributor adds a shared helper module (some come with a new rule new code must follow)
Every change is made by a randomly chosen contributor persona, like a real open-source project.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Level:
    name: str
    rate: float
    weights: dict = field(default_factory=dict)
    max_events: int = 12
    description: str = ""


LEVELS = {
    "static": Level("static", 0.0, {}, 0, "control: nobody touches the repository"),
    "light": Level("light", 0.3, {"cosmetic": 0.6, "rename": 0.2, "issue": 0.2}, 4,
                   "occasional changes, mostly cosmetic"),
    "moderate": Level("moderate", 0.6, {"cosmetic": 0.25, "rename": 0.35, "issue": 0.2, "close_issue": 0.05,
                                        "revert": 0.05, "pr_open": 0.05, "pr_merge": 0.05, "add_helper": 0.05}, 8,
                      "a steady stream of mixed changes"),
    "heavy": Level("heavy", 1.2, {"rename": 0.45, "issue": 0.2, "cosmetic": 0.1, "close_issue": 0.05,
                                  "revert": 0.05, "pr_open": 0.1, "pr_merge": 0.05, "add_helper": 0.05}, 12,
                   "frequent breaking renames and requirement changes"),
    "churn": Level("churn", 1.0, {"rename": 0.2, "revert": 0.25, "issue": 0.15, "close_issue": 0.2,
                                  "pr_open": 0.1, "pr_reject": 0.1}, 12,
                   "state reconciliation stress: reverts, withdrawn issues, rejected PRs"),
}
