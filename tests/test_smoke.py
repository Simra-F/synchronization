"""Offline tests (mock LLM, in-memory repo). Run: python -m pytest -q   (or: python tests/test_smoke.py)"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxdrift.llm import LLM  # noqa: E402
from ctxdrift.pipeline import run_chain, stages  # noqa: E402
from ctxdrift.repo import LocalRepo  # noqa: E402
from ctxdrift.pipeline import parse_files  # noqa: E402
from ctxdrift.repo import three_way  # noqa: E402
from ctxdrift.run_experiment import main, run_node_tests, run_one  # noqa: E402
from ctxdrift.source_of_truth import SourceOfTruth  # noqa: E402
from ctxdrift.symbols import extract, rename  # noqa: E402
from ctxdrift.task import CONTRIBUTORS, FACTS, FOCUS_FILES, HELPERS, KEYWORDS, REQUIREMENTS, TASK, load_seed  # noqa: E402
from ctxdrift.tracker import evaluate  # noqa: E402
from github_modifier.modifier import Modifier, Truth  # noqa: E402
from github_modifier.scenarios import LEVELS, Level  # noqa: E402

SEED = load_seed()


def test_seed_symbols():
    assert {"loadTasks", "BUTTONS", "STORAGE_KEY", "renderTasks"} <= set(extract(SEED))


def test_v_rename_then_revert_nets_to_zero():
    repo = LocalRepo(SEED)
    v = SourceOfTruth(repo, KEYWORDS)
    repo.commit("rename", rename(repo.files(), "loadTasks", "fetchTasks"))
    changes = v.diff(0)
    assert [c.kind for c in changes] == ["rename"] and "fetchTasks" in changes[0].text
    assert v.lookup("loadTasks").startswith("`loadTasks` no longer exists")
    repo.commit("revert", rename(repo.files(), "fetchTasks", "loadTasks"))
    assert v.diff(0) == []                                  # net change is nothing
    assert v.relevant(v.diff(1), "uses fetchTasks")          # but an agent synced at v1 is stale


def test_v_issue_relevance_and_cosmetic_ignored():
    repo = LocalRepo(SEED)
    v = SourceOfTruth(repo, KEYWORDS)
    repo.commit("style", {"styles/main.css": SEED["styles/main.css"].replace("#2f6fde", "#be123c")})
    assert v.diff(0) == []                                  # cosmetic change: no fact changed
    n = repo.open_issue(REQUIREMENTS[0].title, REQUIREMENTS[0].body)
    assert len(v.relevant(v.diff(0), "")) == 1
    repo.close_issue(n)
    assert v.diff(0) == []                                  # opened and withdrawn: nothing to tell
    v.reconcile()
    assert v.missed == 0


def test_tracker_detects_stale_names_and_issue_overrides():
    truth = Truth(extract(SEED), FACTS)
    truth.rename("loadTasks", "fetchTasks")
    truth.requirements[1] = REQUIREMENTS[0]                 # JSONL replaces CSV
    snap = truth.snapshot()
    bad = evaluate("const rows = loadTasks(); BUTTONS.Export = x; // csv, node:test", snap)
    good = evaluate("const rows = fetchTasks(); BUTTONS.Export = x; // jsonl, node:test", snap)
    assert bad["stale_mentions"] == ["loadTasks"] and "export_format" in bad["failed_facts"]
    assert good["info_loss"] == 0


def test_modifier_lockstep_all_ops():
    repo = LocalRepo(SEED)
    truth = Truth(extract(SEED), FACTS)
    level = Level("all", 3.0, {op: 1 for op in ["rename", "cosmetic", "issue", "close_issue", "revert",
                                                 "pr_open", "pr_merge", "pr_reject"]}, 40)
    level.weights["add_helper"] = 1
    m = Modifier(repo, truth, level, REQUIREMENTS, seed=1, helpers=HELPERS, contributors=CONTRIBUTORS)
    for _ in range(10):
        m.step()
    assert len(m.log) >= 20 and not any("error" in r for r in m.log)
    assert set(truth.current.values()) <= set(extract(repo.files()))      # truth matches the repo
    assert len({r["author"] for r in m.log}) > 1                          # several contributors
    assert run_node_tests(repo.files()) in (True, None)                   # the site's own tests still pass


def test_v_beats_no_sync_on_heavy_changes():
    llm = LLM(backend="mock")
    result = {}
    for mode in ("none", "v"):
        repo = LocalRepo(SEED)
        truth = Truth(extract(SEED), FACTS)
        m = Modifier(repo, truth, LEVELS["heavy"], REQUIREMENTS, seed=3)
        rows, _ = run_chain(llm, repo, TASK, truth, KEYWORDS, FOCUS_FILES, mode=mode, lockstep=m)
        assert [r["role"] for r in rows] == stages()
        result[mode] = sum(r["info_loss"] for r in rows)
    assert result["v"] < result["none"]


def test_lean_briefs_and_code_fixes():
    from ctxdrift.pipeline import _fix_names
    repo = LocalRepo(SEED)
    v = SourceOfTruth(repo, KEYWORDS)
    repo.commit("r", rename(repo.files(), "loadTasks", "fetchTasks"))
    brief = v.brief(v.version, repo.files())
    assert "src/storage.js: STORAGE_KEY, addTask, fetchTasks" in brief and "RULES (CONTRIBUTING.md):" in brief
    out = "const rows = loadTasks();"
    assert _fix_names(out, v.divergence(out, 0, TASK)) == "const rows = fetchTasks();"   # fixed by code, 0 tokens
    llm = LLM(backend="mock")
    tokens = {}
    for mode in ("none", "lean"):
        repo = LocalRepo(SEED)
        truth = Truth(extract(SEED), FACTS)
        m = Modifier(repo, truth, LEVELS["heavy"], REQUIREMENTS, seed=3, helpers=HELPERS)
        rows, final = run_chain(llm, repo, TASK, truth, KEYWORDS, FOCUS_FILES, mode=mode, lockstep=m)
        tokens[mode] = sum(r["prompt_tokens"] for r in rows)
        if mode == "lean":
            assert not evaluate(final, truth.snapshot())["stale_mentions"]   # the PR text has no stale names
    assert tokens["lean"] < 0.8 * tokens["none"]


def test_lean2_and_ci_check():
    from ctxdrift.run_experiment import ci_problems
    repo = LocalRepo(SEED)
    truth = Truth(extract(SEED), FACTS)
    m = Modifier(repo, truth, LEVELS["heavy"], REQUIREMENTS, seed=3, helpers=HELPERS)
    rows, _ = run_chain(LLM(backend="mock"), repo, TASK, truth, KEYWORDS, FOCUS_FILES, mode="lean2", lockstep=m)
    assert len(rows) == 7
    bad = {"src/components/toolbar.js": 'const BUTTONS = {};\n', "tests/export.test.js": 'test("x", () => {});\n'}
    problems = ci_problems(SEED, bad)
    assert any("handleAdd" in p for p in problems)
    if shutil.which("node"):
        assert any("node --test fails" in p for p in problems)


def test_parse_files():
    text = "toolbar.js\n```js\nconst BUTTONS = {};\n```\n**tests/export.test.js**\n```javascript\ntest()\n```"
    assert set(parse_files(text, SEED)) == {"src/components/toolbar.js", "tests/export.test.js"}


def test_three_way_merge():
    base = "a\nb\nc\nd\ne\n"
    merged, ok = three_way(base, "A\nb\nc\nd\ne\n", "a\nb\nc\nd\nE\n")
    assert ok and merged == "A\nb\nc\nd\nE\n"
    assert not three_way(base, "X\nb\nc\nd\ne\n", "Y\nb\nc\nd\ne\n")[1]


def test_focus_relevance_and_time_trigger():
    repo = LocalRepo(SEED)
    v = SourceOfTruth(repo, KEYWORDS)
    repo.commit("r1", rename(repo.files(), "removeTask", "deleteTask"))    # storage.js is a focus file
    repo.commit("r2", rename(repo.files(), "THEMES", "THEME_LIST"))       # theme.js is not
    assert [c.names[0] for c in v.relevant(v.diff(0), TASK, FOCUS_FILES)] == ["removeTask"]
    llm = LLM(backend="mock")
    for t_max, expect in ((None, False), (1, True)):
        repo = LocalRepo(SEED)
        truth = Truth(extract(SEED), FACTS)
        m = Modifier(repo, truth, Level("one", 20.0, {"issue": 1}, 1), REQUIREMENTS, seed=0)   # one relevant issue at hop 1
        rows, _ = run_chain(llm, repo, TASK, truth, KEYWORDS, FOCUS_FILES, mode="v", s=5, t_max=t_max,
                            lockstep=m, rounds=1)
        assert any(r["trigger"] == "time" for r in rows) == expect


def test_feature_pr_is_auto_merged():
    os.environ["SIM_PRESET"] = "small"
    llm = LLM(backend="mock")
    llm.chat = lambda system, user, agent="", **k: (
        ("src/components/toolbar.js\n```js\n" + SEED["src/components/toolbar.js"].replace(
            '"Theme": nextTheme', '"Theme": nextTheme, "Export": () => loadTasks()') + "```") if agent == "lead" else "ok",
        {"prompt_tokens": 1, "completion_tokens": 1, "tool_calls": 0, "seconds": 0.0})
    rows, meta = run_one("static", "none", 0, {"lockstep": True}, llm, llm, SEED)
    assert meta["feature"]["merged"] is True and meta["feature"]["tests_pass"] in (True, None)


def test_sim_backend_and_sweep(tmp_path):
    from s_values_testing.sweep import main as sweep_main
    sweep_main(["--backend", "sim", "--runs", "2", "--s-values", "0,inf", "--t-values", "off,1",
                "--scenarios", "heavy", "--out", str(tmp_path)])
    out = next(tmp_path.iterdir())
    assert {"recommendation.json", "pareto.png", "retained_vs_s.png", "heatmap_s_t.png"} <= {p.name for p in out.iterdir()}


def test_full_experiment_writes_graphs(tmp_path):
    main(["--backend", "mock", "--runs", "1", "--scenarios", "static,heavy", "--out", str(tmp_path)])
    out = next(tmp_path.iterdir())
    assert {"info_loss_vs_hop.png", "staleness_vs_hop.png", "per_agent_loss.png", "cost.png"} <= {p.name for p in out.iterdir()}


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(Path(tempfile.mkdtemp())) if "tmp_path" in fn.__code__.co_varnames else fn()
            print("PASS", name)
