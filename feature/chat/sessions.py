"""只保存会话目录；消息历史由 LangGraph 的 checkpointer 独立保存。"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from uuid import uuid4


@dataclass(frozen=True)
class Session:
    thread_id: str
    title: str
    created_at: str


class SessionStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        try:
            with self.connection:
                self.connection.execute(
                    """CREATE TABLE IF NOT EXISTS sessions (
                        thread_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )"""
                )
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.connection.close()

    def create(self, title: str = "新会话") -> Session:
        session = Session(
            thread_id=str(uuid4()),
            title=title.strip() or "新会话",
            created_at=datetime.now(UTC).isoformat(),
        )
        with self.connection:
            self.connection.execute(
                "INSERT INTO sessions (thread_id, title, created_at) VALUES (?, ?, ?)",
                (session.thread_id, session.title, session.created_at),
            )
        return session

    def list_sessions(self) -> list[Session]:
        rows = self.connection.execute(
            "SELECT thread_id, title, created_at FROM sessions ORDER BY created_at, rowid"
        ).fetchall()
        return [Session(*row) for row in rows]
