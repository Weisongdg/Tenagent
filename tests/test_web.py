"""用 TestClient 验证网页接口，使用临时数据库和假模型，不调用外部服务。"""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from config import Settings
from feature.web import api


class ChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        model="fake",
        api_key="private-test-key",
        data_dir=tmp_path / "chat",
        issue_db_path=tmp_path / "issues.sqlite",
    )
    model = ChatModel(responses=[AIMessage(content="收到"), AIMessage(content="继续")])
    monkeypatch.setattr(api, "create_model", lambda _: model)
    monkeypatch.setattr(api, "create_vector_store", lambda _: (None, Mock()))
    with TestClient(api.create_app(settings), raise_server_exceptions=False) as test_client:
        yield test_client


def test_sessions_chat_and_persistent_history(client):
    first = client.post("/api/sessions", json={"title": "会话一"}).json()["thread_id"]
    second = client.post("/api/sessions", json={"title": "会话二"}).json()["thread_id"]
    endpoint = f"/api/sessions/{first}/messages"
    assert client.post(endpoint, json={"text": "你好"}).json() == {"text": "收到"}
    assert client.get(endpoint).json() == [
        {"role": "user", "text": "你好"},
        {"role": "assistant", "text": "收到"},
    ]
    assert client.get(f"/api/sessions/{second}/messages").json() == []
    assert client.post(endpoint, json={"text": " "}).status_code == 422
    assert client.get("/api/sessions/missing/messages").status_code == 404


def test_import_query_and_report(client, tmp_path, monkeypatch):
    original = api.generate_report
    monkeypatch.setattr(
        api,
        "generate_report",
        lambda store, filters: original(store, filters, output_dir=tmp_path / "reports"),
    )
    result = client.post("/api/issues/import", json={})
    assert result.status_code == 200
    assert result.json()["total"] == 21
    result = client.post("/api/issues/search", json={"filters": {"area": "L136"}, "limit": 2})
    assert result.json()["total"] == 8
    assert len(result.json()["issues"]) == 2
    assert result.json()["next_offset"] == 2
    report = client.post("/api/reports", json={"area": "L136"}).json()
    assert report["report"]["total"] == 8
    assert report["report"]["open_count"] == 2
    assert "L136" in report["markdown"]


def test_errors_do_not_expose_credentials(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("private-test-key private-provider-response")

    monkeypatch.setattr(api, "create_vector_store", fail)
    result = client.post("/api/vectors/sync")
    assert result.status_code == 500
    assert "RuntimeError" in result.json()["detail"]
    assert "private" not in result.text


def test_static_files_and_api_docs(client):
    assert "Tenagent" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/vendor/marked.umd.js").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/.env").status_code == 404


def test_direct_card_history_hides_internal_tool_messages():
    messages = [
        HumanMessage(content="生成卡片"),
        AIMessage(content="", tool_calls=[{"name": "get_stored_issue", "args": {}, "id": "1"}]),
        ToolMessage(content="**原始卡片**", name="get_stored_issue", tool_call_id="1"),
        ToolMessage(content="内部数据", name="read_excel_issues", tool_call_id="2"),
    ]
    assert api.conversation(messages) == [
        {"role": "user", "text": "生成卡片"},
        {"role": "assistant", "text": "**原始卡片**"},
    ]
