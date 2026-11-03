"""Persistência do histórico de conversa em SQLite (arquivo local, sem servidor).

Guarda o histórico no formato nativo do provedor (para retomar a conversa com
todo o contexto, inclusive chamadas de ferramentas) e um log legível das
mensagens cliente/agente.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / ".sessions" / "conversas.db"


class SessionStore:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                history TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    def new_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def load(self, session_id: str, provider: str) -> list[dict] | None:
        row = self.conn.execute(
            "SELECT provider, history FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != provider:
            raise ValueError(
                f"a sessão {session_id} foi criada com outro provedor ('{row[0]}') e não pode ser retomada"
            )
        return json.loads(row[1])

    def save(self, session_id: str, provider: str, history: list[dict]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO sessions (id, provider, history, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET history = excluded.history, updated_at = excluded.updated_at
            """,
            (session_id, provider, json.dumps(history, ensure_ascii=False, default=str), now, now),
        )
        self.conn.commit()

    def log(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def transcript(self, session_id: str) -> list[tuple[str, str]]:
        return self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY rowid", (session_id,)
        ).fetchall()

    def list_sessions(self) -> list[tuple[str, str, str]]:
        return self.conn.execute(
            "SELECT id, provider, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
