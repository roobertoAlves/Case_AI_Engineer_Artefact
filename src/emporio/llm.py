"""Provedor de LLM com tool calling: Ollama (modelo local).

O provedor guarda o histórico no seu formato nativo (lista de dicts
serializável em JSON), para que possa ser persistido e retomado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from emporio.tools import ToolSpec


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str
    history: list[dict]

    def add_user_message(self, text: str) -> None: ...
    def step(self, system: str, context: str, tools: list[ToolSpec]) -> Turn: ...
    def add_tool_results(self, results: list[tuple[ToolCall, str]]) -> None: ...


class OllamaProvider:
    """Modelo local via Ollama (padrão: qwen3.5:4b), com tool calling nativo."""

    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None):
        import ollama

        self.client = ollama.Client(host=host or os.getenv("OLLAMA_HOST", "http://localhost:11434"))
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
        # Thinking ligado melhora a escolha de ferramentas em modelos pequenos; custa latência.
        self.think = os.getenv("OLLAMA_THINK", "true").lower() == "true"
        self.history: list[dict] = []

    def add_user_message(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def step(self, system: str, context: str, tools: list[ToolSpec]) -> Turn:
        response = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": f"{system}\n\n## Contexto atual\n{context}"}, *self.history],
            tools=[
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ],
            think=self.think,
            options={"temperature": 0.2, "num_ctx": 16384},
        )
        msg = response.message
        calls = [
            ToolCall(f"call_{len(self.history)}_{i}", tc.function.name, dict(tc.function.arguments or {}))
            for i, tc in enumerate(msg.tool_calls or [])
        ]
        entry: dict = {"role": "assistant", "content": msg.content or ""}
        if calls:
            entry["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        self.history.append(entry)
        return Turn(msg.content or "", calls)

    def add_tool_results(self, results: list[tuple[ToolCall, str]]) -> None:
        for call, output in results:
            self.history.append({"role": "tool", "tool_name": call.name, "content": output})


def make_provider(name: str | None = None) -> LLMProvider:
    name = (name or os.getenv("LLM_PROVIDER", "ollama")).lower()
    if name == "ollama":
        return OllamaProvider()
    raise ValueError(f"LLM_PROVIDER desconhecido: {name!r} (use 'ollama')")
