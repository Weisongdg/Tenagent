"""聊天功能启动流程：配置、资源管理、Agent 初始化和交互。"""

import sqlite3
from contextlib import ExitStack

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy.exc import SQLAlchemyError

from config import load_settings
from feature.issue_store import IssueStore
from feature.issue_vectors.service import create_vector_store

from .agent import build_agent, create_model
from .cli import run_chat
from .service import ChatService
from .sessions import SessionStore


def run() -> int:
    try:
        settings = load_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            sessions = stack.enter_context(SessionStore(settings.data_dir / "sessions.sqlite"))
            checkpointer = stack.enter_context(
                SqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite"))
            )
            store = stack.enter_context(IssueStore(settings.issue_db_path))
            vectors, client = create_vector_store(settings)
            stack.callback(client.close)
            agent = build_agent(create_model(settings), checkpointer, store, vectors)
            run_chat(ChatService(agent), sessions)
        return 0
    except ValueError as error:
        print(f"配置错误：{error}")
        return 1
    except (OSError, sqlite3.Error, SQLAlchemyError):
        print("无法访问数据目录或数据库，请检查路径和文件权限。")
        return 1
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0
