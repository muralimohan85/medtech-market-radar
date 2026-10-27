"""A small, explicit tool-use loop on the Claude Messages API.

Kept framework-free on purpose: short enough to read end to end,
with hard limits on turns, a single terminal tool that ends the run with a
schema-validated result, and token accounting for cost reporting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from radar.config import MODEL_PRICES

MAX_TOOL_RESULT_CHARS = 8000


class AgentError(RuntimeError):
    def __init__(self, msg: str, usage: "Usage | None" = None, calls: list | None = None):
        super().__init__(msg)
        self.usage, self.calls = usage, calls or []


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, u: Any) -> None:
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0

    def cost(self, model: str) -> float:
        p = MODEL_PRICES.get(model)
        if not p:
            return 0.0
        return round((self.input_tokens * p["input"] + self.output_tokens * p["output"]
                      + self.cache_read_tokens * p["cache_read"] + self.cache_write_tokens * p["cache_write"]) / 1e6, 6)


@dataclass
class AgentRun:
    result: dict
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0


# validate(input) -> (ok, error_message)
Validator = Callable[[dict], tuple[bool, str]]


def _block_to_dict(b: Any) -> dict:
    t = getattr(b, "type", None)
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return b.model_dump() if hasattr(b, "model_dump") else dict(b)


class ClaudeToolAgent:
    def __init__(self, client: Any, model: str, system: str, tool_specs: dict[str, dict],
                 tool_fns: dict[str, Callable[..., Any]], terminal: dict, validate: Validator | None = None,
                 max_turns: int = 8, max_tokens: int = 1500):
        self.client, self.model, self.system = client, model, system
        self.tool_fns, self.terminal, self.validate = tool_fns, terminal, validate
        self.max_turns, self.max_tokens = max_turns, max_tokens
        self.tools = [{"name": n, **s} for n, s in tool_specs.items()] + [terminal]

    def run(self, task: str) -> AgentRun:
        messages: list[dict] = [{"role": "user", "content": task}]
        usage, calls = Usage(), []
        for turn in range(1, self.max_turns + 1):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # the system prompt and tool definitions repeat on every turn and every
                # section, so mark them cacheable
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=self.tools,
                tool_choice={"type": "any"},        # every turn must be a tool call
                messages=messages,
            )
            usage.add(resp.usage)
            blocks = [_block_to_dict(b) for b in resp.content]
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for b in blocks:
                if b.get("type") != "tool_use":
                    continue
                name, args = b["name"], b.get("input") or {}
                if name == self.terminal["name"]:
                    ok, err = self.validate(args) if self.validate else (True, "")
                    calls.append({"tool": name, "args": args, "accepted": ok, "error": err})
                    if ok:
                        return AgentRun(result=args, turns=turn, tool_calls=calls, usage=usage,
                                        cost_usd=usage.cost(self.model))
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
                                    "content": f"Rejected: {err}. Fix and call {name} again."})
                    continue
                fn = self.tool_fns.get(name)
                try:
                    if fn is None:
                        raise KeyError(f"unknown tool {name}")
                    out = fn(**args)
                    content, is_error = json.dumps(out, default=str)[:MAX_TOOL_RESULT_CHARS], False
                except Exception as exc:  # bad arguments, unknown fact, ...
                    content, is_error = f"Error: {exc}", True
                calls.append({"tool": name, "args": args, "error": content if is_error else ""})
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": content,
                                "is_error": is_error})
            if not results:
                results = [{"type": "text", "text": f"Continue the investigation or call {self.terminal['name']}."}]
            messages.append({"role": "user", "content": results})
        raise AgentError(f"no valid {self.terminal['name']} after {self.max_turns} turns", usage, calls)


def make_client(api_key: str | None = None):
    import anthropic

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
