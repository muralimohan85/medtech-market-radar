"""A scripted stand-in for the Anthropic client, so the agent loop is tested
without network access or an API key. It mimics the response objects the SDK
returns (content blocks with .type/.id/.name/.input, .usage, .stop_reason)."""

from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace
from typing import Callable

_ids = itertools.count(1)


def tool_use(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids):04d}", name=name, input=args)


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def response(*blocks, input_tokens=1200, output_tokens=150) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason="tool_use",
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    """`policy(call_index, kwargs) -> response` decides each turn."""

    def __init__(self, policy: Callable[[int, dict], SimpleNamespace]):
        self.policy, self.calls = policy, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        snapshot = copy.deepcopy(kwargs)      # the agent keeps appending to its message list
        self.calls.append(snapshot)
        return self.policy(len(self.calls) - 1, snapshot)


def last_tool_results(kwargs: dict) -> list[dict]:
    msgs = kwargs["messages"]
    if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
        return [b for b in msgs[-1]["content"] if b.get("type") == "tool_result"]
    return []
