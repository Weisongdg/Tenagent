"""配置入口：环境变量优先于项目 .env，所有相对路径基于项目目录。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    model: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    data_dir: Path = PROJECT_DIR / "data" / "chat"
    issue_db_path: Path = PROJECT_DIR / "data" / "issues" / "issues.sqlite"
    embedding_model: str = "ynu-rzko/Qwen3-Embedding-4B"


def load_settings(project_dir: Path = PROJECT_DIR) -> Settings:
    values = {**dotenv_values(project_dir / ".env"), **os.environ}
    model = (values.get("OPENAI_MODEL") or "").strip()
    api_key = (values.get("OPENAI_API_KEY") or "").strip()
    if not model or not api_key:
        raise ValueError("请复制 .env.example 为 .env，并填写 OPENAI_MODEL 和 OPENAI_API_KEY。")

    data_dir = Path(values.get("CHAT_DATA_DIR") or "data/chat").expanduser()
    if not data_dir.is_absolute():
        data_dir = project_dir / data_dir
    issue_db_path = Path(values.get("ISSUE_DB_PATH") or "data/issues/issues.sqlite").expanduser()
    if not issue_db_path.is_absolute():
        issue_db_path = project_dir / issue_db_path
    return Settings(
        model=model,
        api_key=api_key,
        base_url=(values.get("OPENAI_BASE_URL") or "").strip() or None,
        data_dir=data_dir.resolve(),
        issue_db_path=issue_db_path.resolve(),
        embedding_model=(values.get("EMBEDDING_MODEL") or "ynu-rzko/Qwen3-Embedding-4B").strip(),
    )
