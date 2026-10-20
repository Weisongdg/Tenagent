"""真实 SQLite/SQLModel 和 Agent 工具集成；模型仅使用离线替身。"""

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from feature.chat import ChatService, build_agent
from feature.excel_reader import read_issues
from feature.excel_reader.reader import DEFAULT_FILE
from feature.issue_analysis.evidence import locate_evidence
from feature.issue_analysis.renderer import render_issue_card
from feature.issue_analysis.schemas import IssueAnalysis, IssueAnalysisResult
from feature.issue_analysis.tools import create_analysis_tools
from feature.issue_store import IssueStore, IssueStoreError
from feature.issue_store.tools import create_store_tools


class RecordingModel(FakeMessagesListChatModel):
    received: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def source():
    return read_issues(DEFAULT_FILE)


@pytest.fixture
def result(source):
    issue = source.issues[5]
    analysis = IssueAnalysis(
        title="钢珠掉落",
        summary={
            "text": "记录钢珠掉落。",
            "evidence": [{"field_name": "problem_description", "quote": issue.problem_description}],
        },
        causes=[],
        actions=[],
        observations=[],
        verifications=[],
        missing_information=[],
        warnings=[],
    )
    return IssueAnalysisResult(
        source_file=source.source_file,
        sheet_name=source.sheet_name,
        column_mapping=source.column_mapping,
        issue=issue,
        analysis=analysis,
        evidence_locations=locate_evidence(issue, analysis, source.column_mapping),
    )


def call(tool, args):
    return tool.invoke({"type": "tool_call", "name": tool.name, "id": str(uuid4()), "args": args})


def test_real_workbook_import_is_idempotent_and_survives_restart(tmp_path, source):
    path = tmp_path / "issues.sqlite"
    with IssueStore(path) as store:
        first = store.import_excel()
        assert (first.total, first.inserted, first.updated, first.skipped) == (21, 21, 0, 0)
        original = store.get_issue(6)
        second = store.import_excel()
        assert (second.inserted, second.updated, second.skipped) == (0, 0, 21)
        assert store.get_issue(6).updated_at == original.updated_at
    with IssueStore(path) as store:
        assert store.list_issues().total == 21
        for issue in source.issues:
            saved = store.get_issue(issue.no)
            assert saved.issue.model_dump() == issue.model_dump()
            assert saved.column_mapping == source.column_mapping
            assert saved.analyses == []


def test_analysis_history_is_complete_and_does_not_regenerate(tmp_path, result):
    path = tmp_path / "issues.sqlite"
    card = render_issue_card(result)
    with IssueStore(path) as store:
        first = store.save_analysis(result, card)
        second = store.save_analysis(result, card)
        assert first != second
        assert store.list_issues().total == 1
    with IssueStore(path) as store:
        saved = store.get_issue(6, history_limit=10)
        assert [item.analysis_id for item in saved.analyses] == [second, first]
        assert all(not item.stale and item.review_status == "unreviewed" for item in saved.analyses)
        assert saved.analyses[0].result == result
        assert saved.analyses[0].card == card


@pytest.mark.parametrize("change", ["content", "row", "columns"])
def test_source_changes_make_previous_analysis_stale(tmp_path, source, result, change):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        store.save_analysis(result, render_issue_card(result))
        issue = result.issue
        mapping = dict(source.column_mapping)
        if change == "content":
            issue = issue.model_copy(update={"action": "New action"})
        elif change == "row":
            issue = issue.model_copy(update={"source_row": 100})
        else:
            mapping["action"] = "M"
        changed = source.model_copy(update={"issues": [issue], "column_mapping": mapping})
        imported = store.import_result(changed)
        assert imported.updated == 1
        saved = store.get_issue(6)
        assert saved.issue == issue
        assert saved.analyses[0].stale
        assert saved.analyses[0].result == result
        tool = create_store_tools(store)[2]
        message = call(tool, {"issue_no": 6})
        assert "已过期" in message.text
        assert message.artifact["analyses"][0]["stale"] is True
        assert store.list_issues().total == 21  # 导入子集不会删除其他记录。


@pytest.mark.parametrize("bad_no", [None, True, "", "not-a-number", -1, 1.5])
def test_missing_or_invalid_number_rejects_entire_import(tmp_path, source, bad_no):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        bad = source.issues[1].model_copy(update={"no": bad_no})
        with pytest.raises(IssueStoreError, match="No"):
            store.import_result(source.model_copy(update={"issues": [source.issues[0], bad]}))
        assert store.list_issues().total == 0


def test_duplicate_number_does_not_change_existing_records(tmp_path, source):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        changed = source.issues[0].model_copy(update={"action": "Must not persist"})
        duplicate = changed.model_copy(update={"no": str(changed.no), "source_row": 100})
        with pytest.raises(IssueStoreError, match="重复"):
            store.import_result(source.model_copy(update={"issues": [changed, duplicate]}))
        assert store.get_issue(1).issue == source.issues[0]


def test_database_failure_rolls_back_partial_changes_and_hides_details(
    tmp_path, source, monkeypatch
):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        original_find = store.repository.find
        count = 0

        def failing_find(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise SQLAlchemyError("private-database-parameters")
            return original_find(*args)

        monkeypatch.setattr(store.repository, "find", failing_find)
        changed = source.issues[0].model_copy(update={"action": "Must roll back"})
        with pytest.raises(IssueStoreError, match="回滚") as caught:
            store.import_result(source.model_copy(update={"issues": [changed, source.issues[1]]}))
        assert "private" not in str(caught.value)
        monkeypatch.setattr(store.repository, "find", original_find)
        assert store.get_issue(1).issue == source.issues[0]


def test_source_identity_isolates_files_and_sheets(tmp_path, source):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        other_file = str(tmp_path / "copy.xlsx")
        store.import_result(source.model_copy(update={"source_file": other_file}))
        store.import_result(source.model_copy(update={"sheet_name": "AnotherSheet"}))
        assert store.list_issues().total == 63
        ids = {
            store.get_issue(6).issue_id,
            store.get_issue(6, other_file).issue_id,
            store.get_issue(6, sheet_name="AnotherSheet").issue_id,
        }
        assert len(ids) == 3


def test_raw_cell_types_and_analysis_input_survive_json_storage(tmp_path, source, result):
    issue = result.issue.model_copy(
        update={
            "when": datetime(2026, 10, 20, 9, 30),
            "owner": date(2026, 10, 20),
            "area": time(9, 30),
            "action": timedelta(days=1, seconds=12),
            "impact_qty": 2.0,
            "category": True,
            "root_cause": "=A1",
            "station": "2026-10-20",
        }
    )
    result = result.model_copy(update={"issue": issue})
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.save_analysis(result, render_issue_card(result))
        saved = store.get_issue(6)
        assert saved.issue.model_dump() == issue.model_dump()
        assert saved.analyses[0].result.issue.model_dump() == issue.model_dump()
        for name in type(issue).model_fields:
            assert type(getattr(saved.issue, name)) is type(getattr(issue, name))


def test_query_filters_pagination_and_missing_record(tmp_path, source):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        first = store.list_issues(limit=5)
        second = store.list_issues(offset=5, limit=5)
        assert first.total == second.total == 21
        assert {x.issue_id for x in first.issues}.isdisjoint(x.issue_id for x in second.issues)
        filtered = store.list_issues(product="Summit", status="under tracking")
        expected = [
            x for x in source.issues if x.product == "Summit" and x.status == "under tracking"
        ]
        assert filtered.total == len(expected) > 0
        assert all(x.product == "Summit" and x.status == "under tracking" for x in filtered.issues)
        with pytest.raises(IssueStoreError, match="找不到"):
            store.get_issue(999)


def test_tools_import_and_read_without_reading_source_again(tmp_path, source, monkeypatch):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        tools = {item.name: item for item in create_store_tools(store)}
        assert tools["import_excel_issues"].invoke({})["inserted"] == 21
        assert tools["list_stored_issues"].invoke({"limit": 5})["total"] == 21

        def fail_read(*_):
            raise AssertionError("Saved query must not read Excel")

        monkeypatch.setattr("feature.issue_store.service.read_issues", fail_read)
        saved = call(tools["get_stored_issue"], {"issue_no": 6})
        assert "尚无整理卡片" in saved.text
        assert saved.artifact["issue"] == source.issues[5].model_dump(mode="json")


def test_analysis_tool_auto_saves_and_failure_does_not_add_history(tmp_path, result):
    payload = result.analysis.model_dump()
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "IssueAnalysis",
                        "args": payload,
                        "id": str(uuid4()),
                    }
                ],
            )
        ]
    )
    with IssueStore(tmp_path / "issues.sqlite") as store:
        tool = create_analysis_tools(model, store)[0]
        generated = call(tool, {"issue_no": 6})
        assert generated.status == "success"
        saved = store.get_issue(6)
        assert saved.analyses[0].card == generated.text
        assert saved.analyses[0].result.model_dump(mode="json") == generated.artifact
        payload["summary"]["evidence"][0]["quote"] = "invented quote"
        bad_model = RecordingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "IssueAnalysis",
                            "args": payload,
                            "id": str(uuid4()),
                        }
                    ],
                )
            ]
        )
        failed = call(create_analysis_tools(bad_model, store)[0], {"issue_no": 6})
        assert failed.status == "error"
        assert len(store.get_issue(6, history_limit=100).analyses) == 1


def test_storage_failure_is_tool_error_not_successful_card(tmp_path, result, monkeypatch):
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "IssueAnalysis",
                        "args": result.analysis.model_dump(),
                        "id": str(uuid4()),
                    }
                ],
            )
        ]
    )
    with IssueStore(tmp_path / "issues.sqlite") as store:

        def fail_save(*_):
            raise IssueStoreError("整理结果保存失败，本次结果未入库。")

        monkeypatch.setattr(store, "save_analysis", fail_save)
        failed = call(create_analysis_tools(model, store)[0], {"issue_no": 6})
        assert failed.status == "error"
        assert "未入库" in failed.text
        assert failed.artifact is None
        assert store.list_issues().total == 0


def test_agent_displays_saved_card_without_rewriting_even_with_mixed_tools(tmp_path, result):
    with (
        IssueStore(tmp_path / "issues.sqlite") as store,
        SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver,
    ):
        store.save_analysis(result, render_issue_card(result))
        model = RecordingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_stored_issue", "args": {"issue_no": 6}, "id": str(uuid4())},
                        {"name": "list_stored_issues", "args": {}, "id": str(uuid4())},
                    ],
                ),
                AIMessage(content="普通追问回复"),
            ]
        )
        chat = ChatService(build_agent(model, saver, store))
        answer = chat.reply("saved", "查看问题库中已保存的6号卡片")
        assert len(model.received) == 1
        assert render_issue_card(result) in answer
        assert "未经人工审核" in answer
        assert chat.reply("saved", "谢谢") == "普通追问回复"
        assert len(model.received) == 2


def test_older_analysis_cannot_overwrite_newer_import(tmp_path, source, result):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        before = store.current_fingerprint(6, source.source_file, source.sheet_name)
        changed = result.issue.model_copy(update={"action": "Newly imported action"})
        store.import_result(source.model_copy(update={"issues": [changed]}))
        with pytest.raises(IssueStoreError, match="记录已变化"):
            store.save_analysis(result, render_issue_card(result), before)
        saved = store.get_issue(6)
        assert saved.issue == changed
        assert saved.analyses == []


def test_new_analysis_can_update_unchanged_database_version(tmp_path, source, result):
    with IssueStore(tmp_path / "issues.sqlite") as store:
        store.import_result(source)
        before = store.current_fingerprint(6, source.source_file, source.sheet_name)
        changed = result.issue.model_copy(update={"action": "Fresh Excel action"})
        fresh = result.model_copy(update={"issue": changed})
        store.save_analysis(fresh, render_issue_card(fresh), before)
        saved = store.get_issue(6)
        assert saved.issue == changed
        assert not saved.analyses[0].stale
