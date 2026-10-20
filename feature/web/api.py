"""HTTP 接口适配层；业务逻辑仍由各 feature 的现有服务负责。"""

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from config import PROJECT_DIR, Settings, load_settings
from feature.chat.agent import build_agent, create_model
from feature.chat.middleware import DIRECT_RESULT_TOOLS
from feature.chat.service import ChatService
from feature.chat.sessions import SessionStore
from feature.excel_reader.reader import DEFAULT_FILE, DEFAULT_SHEET
from feature.issue_qa.schemas import QueryFilters
from feature.issue_qa.service import IssueQueryService
from feature.issue_report.service import generate_report
from feature.issue_store import IssueStore
from feature.issue_vectors.service import create_vector_store, save_issues


class NewSession(BaseModel):
    title: str = Field(default="新会话", max_length=200)


class ChatInput(BaseModel):
    text: str = Field(min_length=1, max_length=20000, pattern=r"\S")


class ExcelInput(BaseModel):
    file_path: str = DEFAULT_FILE
    sheet_name: str = DEFAULT_SHEET


class SearchInput(BaseModel):
    filters: QueryFilters = Field(default_factory=QueryFilters)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)


def conversation(messages):
    """按轮保留直接返回的卡片，隐藏内部工具调用。"""
    result, turn = [], []
    for message in [*messages, None]:
        if message is None or isinstance(message, HumanMessage):
            direct = [
                item.text
                for item in turn
                if isinstance(item, ToolMessage) and item.name in DIRECT_RESULT_TOOLS
            ]
            answers = [item.text for item in turn if isinstance(item, AIMessage) and item.text]
            if direct or answers:
                result.append(
                    {
                        "role": "assistant",
                        "text": "\n\n".join(direct) if direct else answers[-1],
                    }
                )
            turn = []
            if message is not None:
                result.append({"role": "user", "text": message.text})
        else:
            turn.append(message)
    return result


@contextmanager
def chat_service(settings):
    # 资源在同一请求线程内创建和关闭，不跨线程共享 SQLite 连接。
    with ExitStack() as stack:
        saver = stack.enter_context(
            SqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite"))
        )
        store = stack.enter_context(IssueStore(settings.issue_db_path))
        vectors, client = create_vector_store(settings)
        stack.callback(client.close)
        yield ChatService(build_agent(create_model(settings), saver, store, vectors))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Tenagent API")
    # 本地单用户应用：串行处理写入，避免同一会话同时提交或同步冲突。
    write_lock = Lock()

    @app.exception_handler(Exception)
    async def safe_error(request, error):
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"操作失败（{type(error).__name__}），请检查配置、路径或网络后重试。"
            },
        )

    def require_session(thread_id):
        with SessionStore(settings.data_dir / "sessions.sqlite") as sessions:
            if not any(item.thread_id == thread_id for item in sessions.list_sessions()):
                raise HTTPException(404, "会话不存在。")

    @app.get("/api/sessions")
    def list_sessions():
        with SessionStore(settings.data_dir / "sessions.sqlite") as sessions:
            return [asdict(item) for item in sessions.list_sessions()]

    @app.post("/api/sessions", status_code=201)
    def new_session(body: NewSession):
        with SessionStore(settings.data_dir / "sessions.sqlite") as sessions:
            return asdict(sessions.create(body.title))

    @app.get("/api/sessions/{thread_id}/messages")
    def history(thread_id: str):
        require_session(thread_id)
        # 查看历史不初始化模型或向量库，也不调用 API。
        with SqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")) as saver:
            checkpoint = saver.get_tuple({"configurable": {"thread_id": thread_id}})
            messages = (
                checkpoint.checkpoint["channel_values"].get("messages", []) if checkpoint else []
            )
            return conversation(messages)

    @app.post("/api/sessions/{thread_id}/messages")
    def reply(thread_id: str, body: ChatInput):
        require_session(thread_id)
        with write_lock, chat_service(settings) as service:
            return {"text": service.reply(thread_id, body.text)}

    @app.post("/api/issues/search")
    def search(body: SearchInput):
        with IssueStore(settings.issue_db_path) as store:
            return IssueQueryService(store).search(body.filters, body.offset, body.limit)

    @app.post("/api/issues/import")
    def import_excel(body: ExcelInput):
        with write_lock, IssueStore(settings.issue_db_path) as store:
            return store.import_excel(body.file_path, body.sheet_name)

    @app.post("/api/vectors/sync")
    def sync_vectors():
        with write_lock, IssueStore(settings.issue_db_path) as store:
            vectors, client = create_vector_store(settings)
            try:
                return {"indexed": save_issues(store, vectors)}
            finally:
                client.close()

    @app.post("/api/reports")
    def report(filters: QueryFilters):
        with write_lock, IssueStore(settings.issue_db_path) as store:
            text, result = generate_report(store, filters)
            return {"markdown": text, "report": result}

    # 仅负责分发前端静态文件；页面与 Python 后端通过 /api 通信。
    app.mount("/", StaticFiles(directory=PROJECT_DIR / "frontend", html=True), name="frontend")
    return app
