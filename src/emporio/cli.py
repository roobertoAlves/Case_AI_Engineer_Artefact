"""Interface de linha de comando para conversar com o agente.

Uso:
    emporio                      # nova conversa
    emporio --session abc123     # retoma uma conversa salva
    emporio --debug              # mostra as ferramentas chamadas a cada resposta
    emporio --hoje 2026-02-20    # simula a data atual (útil para testar prazos)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from emporio.agent import Agent
from emporio.llm import make_provider
from emporio.prompts import TZ
from emporio.sessions import SessionStore

EXIT_WORDS = {"sair", "exit", "quit", "tchau"}


def parse_now(value: str | None):
    """Relógio do agente: real, ou uma data/hora simulada (AAAA-MM-DD ou AAAA-MM-DDTHH:MM)."""
    if not value:
        return lambda: datetime.now(TZ)
    fixed = datetime.fromisoformat(value if "T" in value else f"{value}T10:00").replace(tzinfo=TZ)
    return lambda: fixed


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Agente de atendimento da Empório da Música")
    parser.add_argument("--provider", choices=["ollama"], help="padrão: LLM_PROVIDER do .env")
    parser.add_argument("--session", help="id de uma conversa salva para retomar")
    parser.add_argument("--listar", action="store_true", help="lista as conversas salvas")
    parser.add_argument("--debug", action="store_true", help="mostra as chamadas de ferramentas")
    parser.add_argument("--hoje", help="simula a data/hora atual, ex.: 2026-02-20 ou 2026-02-21T15:30")
    args = parser.parse_args(argv)

    console = Console()
    sessions = SessionStore()
    if args.listar:
        for sid, provider, updated in sessions.list_sessions():
            console.print(f"{sid}  {provider:<10} {updated}")
        return 0

    provider = make_provider(args.provider)
    session_id = args.session or sessions.new_id()
    if args.session:
        history = sessions.load(session_id, provider.name)
        if history is None:
            console.print(f"[red]Sessão {session_id} não encontrada.[/red]")
            return 1
        provider.history = history
        for role, content in sessions.transcript(session_id)[-6:]:
            console.print(f"[dim]{'Você' if role == 'user' else 'Duda'}: {content}[/dim]")

    agent = Agent(provider=provider, now=parse_now(args.hoje))
    console.print(Panel.fit(
        "[bold]Empório da Música[/bold] — Sua música começa aqui. 🎸\n"
        f"Assistente virtual Duda · provedor: {provider.name} · sessão: {session_id}\n"
        "Digite 'sair' para encerrar.",
        border_style="yellow",
    ))

    while True:
        try:
            user = console.input("[bold cyan]Você:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() in EXIT_WORDS:
            break
        checkpoint = len(provider.history)
        with console.status("Duda está digitando..."):
            try:
                reply = agent.reply(user)
            except Exception as exc:  # noqa: BLE001 — erro de rede/provedor não deve derrubar o chat
                console.print(f"[red]Erro ao falar com o provedor de LLM: {exc}[/red]")
                del provider.history[checkpoint:]  # descarta o turno incompleto
                continue
        if args.debug:
            for t in reply.tool_calls:
                console.print(f"[dim]  ↳ {t.name}({json.dumps(t.arguments, ensure_ascii=False)})[/dim]")
        console.print("[bold yellow]Duda:[/bold yellow]", Markdown(reply.text))
        sessions.log(session_id, "user", user)
        sessions.log(session_id, "assistant", reply.text)
        sessions.save(session_id, provider.name, provider.history)

    console.print(f"[dim]Conversa salva. Para retomar: emporio --session {session_id}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
