"""Information-loss tracker. Scores each sub-agent output against the GROUND TRUTH at the moment the agent acted.
Ground truth comes from the modifier's own action log (github_modifier.modifier.Truth), never from V, so the
system under test cannot grade itself.

Per output:
  retention  = share of required facts that hold (original task constraints + requirements from open issues,
               with symbol names resolved to their CURRENT names)
  stale_rate = stale symbol mentions / all tracked symbol mentions (stale = renamed away, reverted away,
               or only proposed in an unmerged/rejected PR)
  info_loss  = 1 - retention * (1 - stale_rate)          (0 = nothing lost, 1 = everything lost)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, replace

from .symbols import mentions

PLACEHOLDER = re.compile(r"<<(\w+)>>")


@dataclass
class Fact:
    name: str
    description: str
    any_of: list = field(default_factory=list)    # at least one must match
    none_of: list = field(default_factory=list)   # none may match

    def resolve(self, current_names: dict) -> "Fact":
        sub = lambda p: PLACEHOLDER.sub(lambda m: re.escape(current_names.get(m.group(1), m.group(1))), p)
        return replace(self, any_of=[sub(p) for p in self.any_of], none_of=[sub(p) for p in self.none_of])

    def holds(self, text: str) -> bool:
        if any(re.search(p, text, re.I | re.M) for p in self.none_of):
            return False
        return not self.any_of or any(re.search(p, text, re.I | re.M) for p in self.any_of)


@dataclass
class Requirement:
    """A requirement change delivered as a GitHub issue. Its fact replaces (or adds to) the fact of the same name
    while the issue is open; closing the issue withdraws it."""
    title: str
    body: str
    fact: Fact


def evaluate(text: str, truth: dict) -> dict:
    facts = truth["facts"]
    failed = [f.name for f in facts if not f.holds(text)]
    retention = 1 - len(failed) / len(facts) if facts else 1.0
    stale = sorted(n for n in truth["stale"] if mentions(n, text))
    current = sorted(n for n in truth["current"] if mentions(n, text))
    stale_rate = len(stale) / (len(stale) + len(current)) if (stale or current) else 0.0
    return {"info_loss": 1 - retention * (1 - stale_rate), "retention": retention, "stale_rate": stale_rate,
            "failed_facts": failed, "stale_mentions": stale}
