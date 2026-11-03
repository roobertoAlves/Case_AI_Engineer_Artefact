"""Loop do agente: LLM decide quais ferramentas chamar até ter a resposta final."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from emporio.data import Store
from emporio.llm import LLMProvider, ToolCall, make_provider
from emporio.policies import get_retriever
from emporio.prompts import SYSTEM_PROMPT, TZ, dynamic_context
from emporio.tools import Toolbox

MAX_TOOL_ROUNDS = 6
FALLBACK_REPLY = (
    "Opa, me enrolei aqui para encontrar essa informação. Pode reformular a pergunta? "
    "Se preferir, fale com a equipe pelo (67) 3341-4444."
)


@dataclass
class ToolTrace:
    name: str
    arguments: dict
    output: str


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolTrace] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        store: Store | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(TZ),
    ):
        self.provider = provider or make_provider()
        self.now = now
        self.toolbox = Toolbox(store or Store(), get_retriever(), today=lambda: self.now().date())

    def reply(self, user_message: str) -> Reply:
        self.provider.add_user_message(user_message)
        traces: list[ToolTrace] = []
        for _ in range(MAX_TOOL_ROUNDS):
            turn = self.provider.step(SYSTEM_PROMPT, dynamic_context(self.now()), self.toolbox.specs)
            if not turn.tool_calls:
                return Reply(turn.text.strip() or FALLBACK_REPLY, traces)
            results: list[tuple[ToolCall, str]] = []
            for call in turn.tool_calls:
                output = self.toolbox.run(call.name, call.arguments)
                traces.append(ToolTrace(call.name, call.arguments, output))
                results.append((call, output))
            self.provider.add_tool_results(results)
        return Reply(FALLBACK_REPLY, traces)
