"""Open-weight LLM backends (no Anthropic).
  ollama : local Ollama server, native /api/chat (sets num_ctx, turns thinking off, supports tool calls)
  openai : any OpenAI-compatible server (vLLM, LM Studio, llama.cpp server)
  mock   : offline and deterministic, for tests (an "obedient" agent that applies the updates it is given)
  sim    : behavioural model of a sub-agent with explicit assumptions (ctxdrift/sim_agent.py), for rough
           predictions and parameter sweeps without a GPU; preset via $SIM_PRESET (small | medium | large)
"""
from __future__ import annotations
import json
import os
import re
import threading
import time

import requests

THINK = re.compile(r"<think>.*?</think>\s*", re.S)
DEFAULT_URLS = {"ollama": "http://localhost:11434", "openai": "http://localhost:8000/v1"}


FINISH = ("Use the lookup results above. Now write your COMPLETE answer for your role exactly as instructed (complete files, each preceded by its path, if your role produces code), ending with the FACTS: list.")


class LLM:
    def __init__(self, model=None, backend=None, base_url=None, max_tokens=1500, num_ctx=8192, temperature=0.2):
        self.backend = backend or os.getenv("LLM_BACKEND", "ollama")
        self.model = model or os.getenv("MODEL", "qwen3:1.7b")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_URLS.get(self.backend, "")).rstrip("/")
        self.max_tokens, self.num_ctx, self.temperature = max_tokens, num_ctx, temperature
        self.mock_latency = float(os.getenv("MOCK_LATENCY", "0.02"))
        self.sim_preset, self.sim_seed = os.getenv("SIM_PRESET", "small"), ""
        self.usage: dict = {}
        self._lock = threading.Lock()
        self._no_think = self._no_tools = False

    def chat(self, system, user, agent="agent", tools=None, tool_fn=None, max_tool_rounds=2):
        """Returns (text, stats). tool_fn(name, args) -> str answers the model's tool calls."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        self._agent = agent
        stats = {"prompt_tokens": 0, "completion_tokens": 0, "tool_calls": 0}
        t0 = time.time()
        for rnd in range(max_tool_rounds + 1):
            msg, p, c = self._call(messages, tools if (tool_fn and rnd < max_tool_rounds) else None)
            stats["prompt_tokens"] += p
            stats["completion_tokens"] += c
            calls = msg.get("tool_calls") or []
            if not calls or not tool_fn or rnd == max_tool_rounds:
                break
            messages.append(msg)
            for call in calls:
                fn = call.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                stats["tool_calls"] += 1
                messages.append({"role": "tool", "content": str(tool_fn(fn.get("name"), args)),
                                 "tool_call_id": call.get("id", ""), "tool_name": fn.get("name", "")})
            # Small models often answer a tool result with one line ("yes, that is current") instead of the
            # stage's real output, so restate what the answer must contain.
            messages.append({"role": "user", "content": FINISH})
        text = THINK.sub("", msg.get("content") or "").strip()
        if stats["tool_calls"] and len(text) < 200:           # still no real answer: one plain retry, no tools
            msg, p, c = self._call(messages[:2], None)
            stats["prompt_tokens"] += p
            stats["completion_tokens"] += c
            text = THINK.sub("", msg.get("content") or "").strip()
        stats["seconds"] = time.time() - t0
        with self._lock:
            u = self.usage.setdefault(agent, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "tool_calls": 0})
            u["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "tool_calls"):
                u[k] += stats[k]
        return text, stats

    # --- backends -------------------------------------------------------------------------------
    def _call(self, messages, tools):
        if self.backend == "sim":
            from .sim_agent import sim_reply
            user = messages[1]["content"]
            text = sim_reply(user, self._agent, self.sim_preset, self.sim_seed)
            return {"role": "assistant", "content": text}, len(user) // 4, len(text) // 4
        if self.backend == "mock":
            time.sleep(self.mock_latency)
            user = messages[1]["content"]
            text = _mock_reply(user)
            return {"role": "assistant", "content": text}, len(user) // 4, len(text) // 4
        if self.backend == "ollama":
            return self._ollama(messages, tools)
        return self._openai(messages, tools)

    def _ollama(self, messages, tools):
        body = {"model": self.model, "messages": messages, "stream": False,
                "options": {"num_ctx": self.num_ctx, "num_predict": self.max_tokens, "temperature": self.temperature}}
        if not self._no_think:
            body["think"] = False
        if tools and not self._no_tools:
            body["tools"] = tools
        r = requests.post(f"{self.base_url}/api/chat", json=body, timeout=900)
        if r.status_code == 400 and "think" in r.text:      # model has no thinking switch
            self._no_think = True
            return self._ollama(messages, tools)
        if r.status_code == 400 and "tools" in r.text:      # model has no tool support
            self._no_tools = True
            return self._ollama(messages, None)
        r.raise_for_status()
        j = r.json()
        return j["message"], j.get("prompt_eval_count", 0), j.get("eval_count", 0)

    def _openai(self, messages, tools):
        msgs = [{k: v for k, v in m.items() if k != "tool_name"} for m in messages]
        body = {"model": self.model, "messages": msgs, "max_tokens": self.max_tokens, "temperature": self.temperature}
        if tools and not self._no_tools:
            body["tools"] = tools
        r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=900)
        if r.status_code == 400 and tools:
            self._no_tools = True
            return self._openai(messages, None)
        r.raise_for_status()
        j = r.json()
        u = j.get("usage") or {}
        return j["choices"][0]["message"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _section(text, name):
    m = re.search(rf"^### {name}[^\n]*\n(.*?)(?=^### |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def _mock_reply(user: str) -> str:
    """Restates the task, echoes the repo it was shown with any delivered renames applied, and the updates."""
    repo, updates = _section(user, "REPOSITORY"), _section(user, "REPOSITORY UPDATES")
    for old, new in re.findall(r"`(\w+)` was renamed to `(\w+)`", updates):
        repo = re.sub(rf"\b{old}\b", new, repo)
    return f"TASK: {_section(user, 'TASK').strip()}\n{repo[:3500]}\n{updates}\nFACTS: as above"
