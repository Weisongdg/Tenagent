"""查询/统计使用真实 SQLite；Agent 的工具循环使用离线模型验证。"""

from collections import Counter
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from feature.chat import ChatService, build_agent
from feature.excel_reader import read_issues
from feature.excel_reader.reader import DEFAULT_FILE
from feature.issue_qa import IssueQueryError, IssueQueryService, QueryFilters
from feature.issue_qa.renderer import render_search, render_statistics
from feature.issue_qa.tools import create_qa_tools
from feature.issue_store import IssueStore
from feature.issue_store.models import AnalysisRecord
from tools import build_tools


class RecordingModel(FakeMessagesListChatModel):
    received: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def store(tmp_path):
    with IssueStore(tmp_path / "issues.sqlite") as result:
        yield result


@pytest.fixture
def source(store):
    original = read_issues(DEFAULT_FILE)
    specs = [
        ("L136", "UVI", None, "螺丝安装", "Screw loose", "inspect mount"),
        ("L136", "LS1", "", "SCREW feed", "motor", "安装"),
        ("L166", "LS1", "Closed", "alignment", "mount", "change screw"),
        ("L136", "UVI", "Close", "100%_done", None, None),
        ("L136X", "LS2", "closed", "other", "slower", "unchanged"),
    ]
    issues = [
        original.issues[0].model_copy(
            update={
                "no": number,
                "source_row": 100 + number,
                "product": "Summit",
                "area": area,
                "station": station,
                "status": status,
                "problem_description": problem,
                "root_cause": cause,
                "action": action,
            }
        )
        for number, (area, station, status, problem, cause, action) in enumerate(specs, 1)
    ]
    mapping = dict(original.column_mapping, problem_description="P", root_cause="M", action="Q")
    result = original.model_copy(update={"issues": issues, "column_mapping": mapping})
    store.import_result(result)
    return result


def call(tool, args):
    return tool.invoke({"type": "tool_call", "name": tool.name, "id": str(uuid4()), "args": args})


def test_real_l136_records_match_source_and_original_positions(store):
    source = read_issues(DEFAULT_FILE)
    store.import_result(source)
    result = IssueQueryService(store).search(QueryFilters(area="L136"), limit=100)
    expected = [issue for issue in source.issues if issue.area == "L136"]
    assert result.total == len(expected) == 8
    assert [hit.issue.model_dump() for hit in result.issues] == [x.model_dump() for x in expected]
    text = render_search(result)
    for hit in result.issues:
        assert f"No.{hit.issue_no}" in text
        assert f"第 {hit.issue.source_row} 行" in text
        assert hit.source_file in text and hit.sheet_name in text


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"area": "L136"}, [1, 2, 4]),
        ({"area": "L136", "station": "UVI"}, [1, 4]),
        ({"product": "Summit", "status": "Closed"}, [3]),
        ({"area": "l136"}, []),
        ({"keywords": ["screw"]}, [1, 2, 3]),
        ({"keywords": ["螺丝"]}, [1]),
        ({"keywords": ["screw", "other"], "keyword_mode": "any"}, [1, 2, 3, 5]),
        ({"keywords": ["screw", "mount"], "keyword_mode": "all"}, [1, 3]),
        ({"keywords": ["%"]}, [4]),
        ({"keywords": ["_"]}, [4]),
        ({"keywords": ["%_"]}, [4]),
        ({"keywords": ["' OR 1=1 --"]}, []),
    ],
)
def test_exact_and_keyword_filters_use_same_database_rules(store, source, values, expected):
    result = IssueQueryService(store).search(QueryFilters(**values))
    assert result.total == len(expected)
    assert [hit.issue_no for hit in result.issues] == expected


def test_matching_fields_return_unchanged_text_and_actual_cells(store, source):
    result = IssueQueryService(store).search(QueryFilters(keywords=["screw", "mount"]))
    hit = result.issues[0]
    assert [(match.field_name, match.cell, match.text) for match in hit.matches] == [
        ("root_cause", "M101", "Screw loose"),
        ("action", "Q101", "inspect mount"),
    ]
    assert all(match.text == getattr(hit.issue, match.field_name) for match in hit.matches)
    text = render_search(result)
    assert "任一词命中" in text and "screw、mount" in text
    assert "不等于已经确认其问题分类" in text
    assert "M101" in text and "Screw loose" in text


def test_pagination_counts_all_matches_and_handles_empty_pages(store, source):
    service = IssueQueryService(store)
    first = service.search(QueryFilters(area="L136"), limit=2)
    second = service.search(first.filters, offset=first.next_offset, limit=2)
    assert first.total == second.total == 3
    assert first.next_offset == 2 and second.next_offset is None
    assert [x.issue_no for x in first.issues + second.issues] == [1, 2, 4]
    assert "第 1～2 条" in render_search(first)
    empty_page = service.search(first.filters, offset=20)
    assert empty_page.total == 3 and empty_page.issues == []
    assert "当前页没有记录" in render_search(empty_page)
    zero = service.search(QueryFilters(area="missing"))
    assert zero.total == 0 and "当前条件未检索到" in render_search(zero)


def test_sources_are_filtered_and_equal_numbers_not_merged(store, source, tmp_path, monkeypatch):
    other = source.model_copy(update={"source_file": str(tmp_path / "copy.xlsx")})
    store.import_result(other)
    store.import_result(source.model_copy(update={"sheet_name": "AnotherSheet"}))
    service = IssueQueryService(store)
    assert service.search(QueryFilters()).total == 15
    assert service.search(QueryFilters(file_path=other.source_file)).total == 5
    assert service.search(QueryFilters(sheet_name="AnotherSheet")).total == 5
    monkeypatch.chdir(tmp_path)
    # 相对路径始终相对于项目目录。
    selected = service.search(QueryFilters(file_path=DEFAULT_FILE, sheet_name=source.sheet_name))
    assert selected.total == 5
    assert selected.filters.file_path == source.source_file


def test_grouped_counts_match_program_reference_with_null_and_raw_statuses(store, source):
    service = IssueQueryService(store)
    stats = service.summarize(QueryFilters(), "status")
    expected = Counter(issue.status for issue in source.issues)
    assert {group.value: group.count for group in stats.groups} == dict(expected)
    assert stats.total == sum(expected.values()) == 5
    assert stats.total_groups == 5
    assert set(group.value for group in stats.groups) == {None, "", "Close", "Closed", "closed"}
    for group in stats.groups:
        assert group.examples
        assert all(hit.issue.status == group.value for hit in group.examples)
    text = render_statistics(stats)
    assert "未填写" in text and "空字符串" in text
    assert "仅为该组部分依据" in text
    assert "No." in text and "第 101 行" in text


def test_statistics_pagination_examples_and_keyword_filters(store, source):
    service = IssueQueryService(store)
    stats = service.summarize(QueryFilters(), "area", limit=1)
    assert stats.total == 5 and stats.total_groups == 3 and stats.next_offset == 1
    assert stats.groups[0].value == "L136" and stats.groups[0].count == 3
    assert len(stats.groups[0].examples) == 2
    assert all(hit.issue.area == "L136" for hit in stats.groups[0].examples)
    assert "尚有后续分组" in render_statistics(stats)
    keyword = QueryFilters(keywords=["screw"], area="L136")
    filtered = service.summarize(keyword, "station")
    assert filtered.total == service.search(keyword).total == 2
    assert sum(group.count for group in filtered.groups) == 2
    assert all(hit.matches for group in filtered.groups for hit in group.examples)
    zero = service.summarize(QueryFilters(area="missing"), "area")
    assert zero.total == zero.total_groups == 0 and not zero.groups
    assert "当前条件未检索到" in render_statistics(zero)


def test_analysis_history_never_increases_issue_count(store, source):
    issue_id = store.get_issue(1).issue_id
    with store.repository.transaction() as session:
        for _ in range(3):
            session.add(
                AnalysisRecord(
                    issue_id=issue_id,
                    fingerprint="test",
                    schema_version=2,
                    result_json={},
                    cell_types={},
                    card="test",
                )
            )
    service = IssueQueryService(store)
    assert service.search(QueryFilters()).total == 5
    assert service.summarize(QueryFilters(), "area").total == 5


@pytest.mark.parametrize(
    "values",
    [
        {"keywords": [" "]},
        {"keywords": [""]},
        {"area": " "},
        {"keyword_mode": "bad"},
        {"unexpected": 1},
        {"keywords": [str(x) for x in range(11)]},
    ],
)
def test_invalid_filters_are_rejected(values):
    with pytest.raises(ValidationError):
        QueryFilters(**values)


def test_invalid_page_and_group_rejected(store):
    service = IssueQueryService(store)
    with pytest.raises(IssueQueryError):
        service.search(QueryFilters(), limit=0)
    with pytest.raises(IssueQueryError):
        service.summarize(QueryFilters(), "root_cause")


def test_tools_do_not_read_excel_and_sanitize_database_failure(store, source, monkeypatch):
    def fail_read(*_):
        raise AssertionError("Query must not read Excel")

    monkeypatch.setattr("feature.issue_store.service.read_issues", fail_read)
    tools = {tool.name: tool for tool in create_qa_tools(store)}
    message = call(tools["search_issues"], {"filters": {"area": "L136"}})
    assert message.status == "success" and message.artifact["total"] == 3
    stats = call(tools["summarize_issues"], {"filters": {}, "group_by": "area"})
    assert stats.status == "success" and stats.artifact["total"] == 5

    def fail_query(*_):
        raise SQLAlchemyError("private-sql-parameters")

    monkeypatch.setattr("feature.issue_qa.repository.QueryRepository.count", fail_query)
    error = call(tools["search_issues"], {"filters": {}})
    assert error.status == "error" and error.artifact is None
    assert "private" not in error.text and "查询失败" in error.text


@pytest.mark.parametrize(
    "name, args",
    [
        ("search_issues", {"filters": {"area": "L136"}, "limit": 2}),
        ("summarize_issues", {"filters": {"area": "L136"}, "group_by": "status"}),
    ],
)
def test_agent_keeps_fixed_results_with_mixed_tools_and_restores_history(
    store, source, tmp_path, name, args
):
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": str(uuid4())},
                    {"name": "list_stored_issues", "args": {}, "id": str(uuid4())},
                ],
            ),
            AIMessage(content="普通追问"),
        ]
    )
    assert name in {tool.name for tool in build_tools(model, store)}
    path = str(tmp_path / "chat.sqlite")
    with SqliteSaver.from_conn_string(path) as saver:
        chat = ChatService(build_agent(model, saver, store))
        answer = chat.reply("qa", "L136 有哪些问题或状态分布？")
        assert len(model.received) == 1
        message = next(
            item for item in chat.history("qa") if item.type == "tool" and item.name == name
        )
        assert answer == message.text
        assert message.artifact["total"] == 3
        assert "No." in answer
        assert chat.reply("qa", "谢谢") == "普通追问"
    with SqliteSaver.from_conn_string(path) as saver:
        chat = ChatService(build_agent(model, saver, store))
        restored = next(
            item for item in chat.history("qa") if item.type == "tool" and item.name == name
        )
        assert restored.content == answer and restored.artifact == message.artifact
