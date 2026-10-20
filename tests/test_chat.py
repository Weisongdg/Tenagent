"""用真实 Agent 和 SQLite、假模型验证会话行为，不发送网络请求。"""

from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from config import load_settings
from feature.chat import ChatService, SessionStore, build_agent
from feature.chat.cli import run_chat
from feature.chat.service import ChatError


class RecordingModel(FakeMessagesListChatModel):
    """记录真正传给模型的上下文，也能模拟上游失败。"""

    received: list[list[BaseMessage]] = Field(default_factory=list)
    fail: bool = False

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        # 纯聊天回归：允许注册真实工具，但此假模型只返回文字，不发起工具调用。
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        if self.fail:
            raise RuntimeError("private-provider-response")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def make_model() -> RecordingModel:
    return RecordingModel(
        responses=[AIMessage(content=f"回答 {i}", id=str(uuid4())) for i in range(10)]
    )


def human_texts(messages: list[BaseMessage]) -> list[str]:
    return [message.text for message in messages if message.type == "human"]


def test_context_is_restored_without_duplicate_messages(tmp_path: Path):
    model = make_model()
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        service.reply("a", "我叫小明")
        service.reply("a", "我叫什么？")

        assert human_texts(model.received[-1]) == ["我叫小明", "我叫什么？"]
        assert [message.type for message in service.history("a")] == ["human", "ai", "human", "ai"]
        assert model.received[-1][0].type == "system"


def test_sessions_are_isolated_and_can_be_switched_back(tmp_path: Path):
    model = make_model()
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        service.reply("a", "A 的内容")
        service.reply("b", "B 的内容")
        assert human_texts(model.received[-1]) == ["B 的内容"]
        service.reply("a", "继续 A")
        assert human_texts(model.received[-1]) == ["A 的内容", "继续 A"]
        assert human_texts(service.history("b")) == ["B 的内容"]


def test_sessions_and_context_survive_closed_connections(tmp_path: Path):
    metadata_path = tmp_path / "sessions.sqlite"
    checkpoint_path = str(tmp_path / "checkpoints.sqlite")
    with (
        SessionStore(metadata_path) as store,
        SqliteSaver.from_conn_string(checkpoint_path) as saver,
    ):
        session = store.create("项目讨论")
        service = ChatService(build_agent(make_model(), saver))
        service.reply(session.thread_id, "需要记住的内容")

    # 重建连接、模型及 Agent，模拟退出后重新启动。
    model = make_model()
    with (
        SessionStore(metadata_path) as store,
        SqliteSaver.from_conn_string(checkpoint_path) as saver,
    ):
        restored = store.list_sessions()
        assert restored == [session]
        service = ChatService(build_agent(model, saver))
        assert human_texts(service.history(session.thread_id)) == ["需要记住的内容"]
        service.reply(session.thread_id, "继续上次")
        assert human_texts(model.received[-1]) == ["需要记住的内容", "继续上次"]
        assert len(service.history(session.thread_id)) == 4


def test_failed_call_preserves_history_and_does_not_fabricate_reply(tmp_path: Path):
    model = make_model()
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        service.reply("a", "成功的一轮")
        model.fail = True
        with pytest.raises(ChatError) as caught:
            service.reply("a", "失败的一轮")
        assert "private-provider-response" not in str(caught.value)
        assert [message.type for message in service.history("a")] == ["human", "ai", "human"]
        model.fail = False
        service.reply("a", "继续对话")
        assert human_texts(model.received[-1]) == ["成功的一轮", "失败的一轮", "继续对话"]
        assert len(service.history("a")) == 5


def test_cli_commands_do_not_call_model_and_invalid_selection_keeps_session(tmp_path: Path):
    model = make_model()
    commands = iter(
        [
            "/new 第二个",
            "/list",
            "/use 0",
            "/use abc",
            "/use 1",
            "/history",
            "第一会话的问题",
            "/use 2",
            "/history",
            "/help",
            "/unknown",
            "/exit",
        ]
    )
    output = []
    with (
        SessionStore(tmp_path / "sessions.sqlite") as store,
        SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver,
    ):
        service = ChatService(build_agent(model, saver))
        run_chat(service, store, input_fn=lambda _: next(commands), output=output.append)
        sessions = store.list_sessions()
        assert len(sessions) == 2
        assert human_texts(service.history(sessions[0].thread_id)) == ["第一会话的问题"]
        assert service.history(sessions[1].thread_id) == []
    assert len(model.received) == 1
    assert any("序号超出范围" in line for line in output)
    assert any("未知命令" in line for line in output)


def test_cli_can_choose_existing_session_and_recover_after_failed_call(tmp_path: Path):
    model = make_model()
    model.fail = True
    commands = iter(["99", "1", "会失败的问题", "/history", "/list", "/exit"])
    output = []
    with (
        SessionStore(tmp_path / "sessions.sqlite") as store,
        SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver,
    ):
        session = store.create("已有会话")
        service = ChatService(build_agent(model, saver))
        run_chat(service, store, input_fn=lambda _: next(commands), output=output.append)
        assert human_texts(service.history(session.thread_id)) == ["会失败的问题"]
    assert any("聊天调用失败" in line for line in output)
    assert any("你：会失败的问题" in line for line in output)
    assert any("已有会话" in line for line in output)


def test_empty_message_does_not_change_history(tmp_path: Path):
    model = make_model()
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        with pytest.raises(ValueError, match="消息不能为空"):
            service.reply("a", "  ")
        assert service.history("a") == []
        assert model.received == []


def test_settings_read_project_env_and_environment_takes_priority(tmp_path: Path, monkeypatch):
    for key in [
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CHAT_DATA_DIR",
        "ISSUE_DB_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_MODEL=file-model\nOPENAI_API_KEY=test-placeholder\nCHAT_DATA_DIR=local/chat\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    settings = load_settings(tmp_path)
    assert settings.model == "environment-model"
    assert settings.api_key == "test-placeholder"
    assert settings.data_dir == (tmp_path / "local" / "chat").resolve()
    assert settings.issue_db_path == (tmp_path / "data" / "issues" / "issues.sqlite").resolve()
    assert "test-placeholder" not in repr(settings)


def test_missing_configuration_has_actionable_error(tmp_path: Path, monkeypatch):
    for key in ["OPENAI_MODEL", "OPENAI_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="请复制 .env.example"):
        load_settings(tmp_path)
