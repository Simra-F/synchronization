"""Agent 1: the multi-agent system building Feature A.
  planner -> researcher -> (coder -> reviewer) x rounds -> lead (final PR content)

How each stage sees the repository (`mode`):
  none = "regular system": the snapshot taken when the task started, for every stage
  full = re-read the whole repository before every stage (expensive, breaks prefix caching)
  v    = proposed: snapshot + net changes from the source of truth V. A refresh happens when EITHER backup fires:
           count: more than `s` relevant changes are unseen
           time : a relevant change has waited at least `t_max` agent calls
         Relevance prioritises the MAS's focus files (task files + files the agents edit). Agents can also call
         lookup() on V.
  drift = output-checked: a stage runs on the snapshot with no updates up front; its OUTPUT is compared with V
         (by code). Only if it diverges by more than `drift_threshold` facts is it re-run once with V's
         corrections; the corrections then stay in later prompts (V carries them on), else the chain continues.
  hybrid = v + drift: V's updates up front, and the output check as a safety net.
  lean   = the proposed system, cheaper: hybrid with
           #1 role briefs: planner/researcher get V's code-generated fact list instead of raw files;
              coder/reviewer/lead get the fact list plus only the focus files
           #4 code-fixed corrections: stale or PR-only names in an output are replaced by code (0 tokens); other
              divergences (new/withdrawn issues and rules, removed symbols) are sent to the NEXT subagent; only the
              final lead is re-run if it still diverges

Prompt layout (same for all modes, stable parts first so a prefix cache can reuse them):
  TASK | REPOSITORY snapshot | REPOSITORY UPDATES (V only, changes only on refresh) | ROLE | PREVIOUS STAGE
"""
from __future__ import annotations
import re
import time

from .source_of_truth import SourceOfTruth, render_updates
from .symbols import mentions
from .tracker import evaluate

SYSTEM = ("You are a member of a multi-agent software team working on the Taskbox GitHub repository, an open-source "
          "static web app in plain HTML/CSS/JavaScript. Be concise. Use names exactly as they exist in the repository now.")
ROLES = {
    "planner": "PLANNER. Break the task into concrete implementation steps and list the files to change.",
    "researcher": "RESEARCHER. Name the exact files, functions, variables and rules the plan depends on, spelled exactly as in the repository.",
    "coder": "CODER. Write the complete changed files in fenced code blocks, each preceded by its path on its own line.",
    "reviewer": "REVIEWER. Check the code against the task, the repository rules and the current repository state; output the corrected complete files, each preceded by its path.",
    "lead": "LEAD. Produce the final pull request: the complete changed files, each preceded by its path on its own line, then a two-line summary.",
}
FACTS_TAIL = " End with a 'FACTS:' list of the exact function/variable names, files and requirements the next stage must rely on."
LOOKUP_HINT = " If you are not sure a name or requirement is still current, call the lookup tool."
LOOKUP_TOOL = {"type": "function", "function": {
    "name": "lookup",
    "description": "Look up the CURRENT state of a symbol name, file path, or issue/PR number (#n) in the repository's source of truth.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
PATH = r"([\w./-]+\.(?:js|html|css|md|json))"
FILE_BLOCK = re.compile(r"(?:^|\n)[#*`>\s-]*(?:(?:file|path|filename|updated|new file)\s*[:=]?\s*)?[*`]*" + PATH
                        + r"[`*:)\s]*(?:\([^)\n]*\))?[`*:\s]*\n```[\w-]*\n(.*?)```", re.S | re.I)
# A fenced block that names its file in a first-line comment: // src/x.js, /* src/x.css */, <!-- index.html -->
COMMENT_BLOCK = re.compile(r"```[\w-]*\n\s*(?://|/\*|<!--|#)\s*(?:file:?\s*)?" + PATH + r"[^\n]*\n(.*?)```", re.S | re.I)


MODES = ("none", "full", "v", "drift", "hybrid", "lean", "lean2")
LEAN = ("lean", "lean2")


def stages(rounds: int = 2) -> list:
    return ["planner", "researcher"] + ["coder", "reviewer"] * rounds + ["lead"]


def parse_files(text: str, existing=()) -> dict:
    """Files in an answer: a path line followed by a fenced block. `storage.js` maps to `src/storage.js` if unique."""
    out = {}
    found = FILE_BLOCK.findall(text or "")
    labelled = {path for path, _ in found}
    found += [(path, body) for path, body in COMMENT_BLOCK.findall(text or "") if path not in labelled]
    for path, body in found:
        path = path.lstrip("./")
        if existing and path not in existing:
            matches = [p for p in existing if p.endswith("/" + path)]
            path = matches[0] if len(matches) == 1 else path
        out[path] = body
    return out


BRIEF_ONLY = {"lean": ("planner", "researcher"), "lean2": ("planner",)}   # roles that get V's fact list only


def _fix_names(text, changes):
    """Code fix: renamed symbols -> current name, PR-only names -> the name on main."""
    for c in changes:
        wrong, right = c.names[0], c.names[1]
        text = re.sub(rf"(?<![\w$]){re.escape(wrong)}(?![\w$])", right, text)
    return text


def _lcp(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _build(task, header, repo_text, updates, role, mode, lookup, prev_role, prev_text):
    prompt = (f"### TASK\n{task}\n\n### REPOSITORY (snapshot at {header})\n{repo_text}\n\n{updates}"
              f"### YOUR ROLE\n{ROLES[role]}{FACTS_TAIL}{LOOKUP_HINT if mode == 'v' and lookup else ''}\n\n")
    return prompt + (f"### PREVIOUS STAGE ({prev_role})\n{prev_text}\n" if prev_role else "")


def run_chain(llm, repo, task, truth, keywords=(), focus=(), mode="none", s=0, t_max=None, rounds=2, clock=None,
              lockstep=None, lookup=True, reconcile_every=3, drift_threshold=0):
    """Returns (rows, final_text). One row per stage with information-loss, staleness and cost measurements."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {MODES}")
    hop = 0
    if lockstep:                 # reproducible runs measure time in agent calls
        clock_fn, to_calls = (lambda: hop), (lambda dt: dt)
    else:                        # parallel runs measure wall time, converted to "agent calls" by the stage clock
        clock_fn = time.time
        to_calls = lambda dt: dt / (clock.hop_seconds if clock else 20.0)
    v = SourceOfTruth(repo, keywords, clock_fn)   # in none/full mode V only observes (to measure staleness)
    focus = list(focus)
    snapshot, v0, r0 = repo.digest(focus=focus), v.version, repo.version
    snapshot_files = repo.files()
    brief0 = v.brief(v0, snapshot_files) if mode in LEAN else ""
    carry, force_next = [], False                 # lean: corrections deferred to the next subagent
    synced = (v0, r0)                             # V version / repo version the delivered updates reach
    updates, context, prev_role, prev_text, prev_prompt = "", task, None, "", ""
    rows, calls_per_role = [], {}

    for hop, role in enumerate(stages(rounds), 1):
        if lockstep:
            lockstep.step()
        if hop % reconcile_every == 0:
            v.reconcile()

        refreshed, trigger = False, ""
        if mode == "full":
            repo_text, synced, header = repo.digest(focus=focus), (v.version, repo.version), f"v{v.version}"
        else:
            repo_text, header = snapshot, f"v{v0}"
            if mode in LEAN:
                repo_text = brief0
                if role not in BRIEF_ONLY[mode]:
                    repo_text += "".join(f"\n\n--- {p} ---\n{snapshot_files[p]}" for p in focus if p in snapshot_files)
            if mode in ("v", "hybrid") + LEAN:
                pending = v.relevant(v.diff(synced[0]), context, focus)
                oldest = v.oldest_pending(synced[0], context, focus) if pending and t_max is not None else None
                if force_next:
                    trigger, force_next = "correction", False
                elif len(pending) > s:
                    trigger = "count"
                elif oldest is not None and to_calls(clock_fn() - oldest) >= t_max:
                    trigger = "time"
                if trigger:
                    synced, refreshed = (v.version, repo.version), True
                    net = v.relevant(v.diff(v0), context, focus)    # net change since the snapshot, not raw events
                    net += [c for c in carry if c not in net]
                    updates = render_updates(task, net, v0, v.version) if net else ""
        unseen_relevant = len(v.relevant(v.diff(synced[0]), context, focus))
        unseen_events = repo.version - synced[1]

        prompt = _build(task, header, repo_text, updates, role, mode, lookup, prev_role, prev_text)
        truth_now = truth.snapshot()              # grade against what was true when the agent started

        use_tool = mode == "v" and lookup
        text, st = llm.chat(SYSTEM, prompt, agent=role, tools=[LOOKUP_TOOL] if use_tool else None,
                            tool_fn=(lambda name, args: v.lookup(args.get("name", ""))) if use_tool else None)
        recomputed = (len(prompt) - _lcp(prompt, prev_prompt)) // 4   # what a prefix cache must recompute
        divergent, corrected = [], False
        if mode in ("drift", "hybrid"):
            divergent = v.divergence(text, v0, task)
            if len(divergent) > drift_threshold:              # only now: re-run this stage with V's corrections
                synced, corrected, trigger = (v.version, repo.version), True, "drift"
                net = v.relevant(v.diff(v0), context + "\n" + text, focus)
                net += [c for c in divergent if c not in net]
                updates = render_updates(task, net, v0, v.version)
                retry = _build(task, header, repo_text, updates, role, mode, lookup, prev_role, prev_text)
                recomputed += (len(retry) - _lcp(retry, prompt)) // 4
                text, st2 = llm.chat(SYSTEM, retry, agent=role)
                st = {k: st[k] + st2[k] for k in ("prompt_tokens", "completion_tokens", "tool_calls", "seconds")}
                prompt = retry
        code_fixes = deferred = 0
        if mode in LEAN:
            divergent = v.divergence(text, v0, task)
            fixable = [c for c in divergent if c.kind in ("rename", "pr")]
            text = _fix_names(text, fixable)
            code_fixes = len(fixable)
            rest = [c for c in divergent if c not in fixable]
            # lean: re-run only the lead (its output becomes the PR); lean2: re-run any stage that diverges, like hybrid
            if rest and (mode == "lean2" or role == stages(rounds)[-1]):
                corrected, trigger = True, "drift"
                net = v.relevant(v.diff(v0), context + "\n" + text, focus)
                updates = render_updates(task, net + [c for c in carry + rest if c not in net], v0, v.version)
                retry = _build(task, header, repo_text, updates, role, mode, lookup, prev_role, prev_text)
                recomputed += (len(retry) - _lcp(retry, prompt)) // 4
                text, st2 = llm.chat(SYSTEM, retry, agent=role)
                st = {k: st[k] + st2[k] for k in ("prompt_tokens", "completion_tokens", "tool_calls", "seconds")}
                text = _fix_names(text, [c for c in v.divergence(text, v0, task) if c.kind in ("rename", "pr")])
                prompt = retry
            elif rest:                                       # tell the next subagent instead of re-running
                carry += [c for c in rest if c not in carry]
                force_next, deferred = True, len(rest)
            divergent = rest
        if clock:
            clock.update(st["seconds"])
        calls_per_role[role] = calls_per_role.get(role, 0) + 1
        rows.append({
            "hop": hop, "role": role, "role_call": calls_per_role[role],
            **evaluate(text, truth_now),
            "unseen_relevant": unseen_relevant, "unseen_events": unseen_events,
            "refreshed": refreshed or corrected, "trigger": trigger, "update_tokens": len(updates) // 4,
            "divergence": len(divergent), "extra_calls": int(corrected),
            "code_fixes": code_fixes, "deferred": deferred, "handoff_tokens": len(prev_text) // 4,
            "repo_version": repo.version, "v_version": v.version,
            "prompt_tokens": st["prompt_tokens"], "completion_tokens": st["completion_tokens"],
            "recomputed_tokens": recomputed,
            "tool_calls": st["tool_calls"], "seconds": round(st["seconds"], 2), "text": text,
        })
        # focus grows with the files the agents actually write
        for path in parse_files(text, repo.files()):
            if path not in focus:
                focus.append(path)
        context += "\n" + text
        prev_role, prev_text, prev_prompt = role, text, prompt
    return rows, text
