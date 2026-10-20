"""报告数字、范围和不确定性使用真实 SQLite 验证。"""

from collections import Counter

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from feature.chat import ChatService, build_agent
from feature.excel_reader import read_issues
from feature.excel_reader.reader import DEFAULT_FILE
from feature.issue_analysis.evidence import locate_evidence
from feature.issue_analysis.renderer import render_issue_card
from feature.issue_analysis.schemas import IssueAnalysis, IssueAnalysisResult
from feature.issue_qa.schemas import QueryFilters
from feature.issue_report.service import _date_note, generate_report
from feature.issue_store import IssueStore


@pytest.fixture
def store(tmp_path):
    with IssueStore(tmp_path / "issues.sqlite") as value:
        yield value


@pytest.fixture
def source(store):
    source = read_issues(DEFAULT_FILE)
    statuses = ["Closed", "close", "Open", "Under tracking", "done", None, "mystery"]
    dates = [
        "2026-10-20",
        "Oct-30 FY2025",
        "May-05",
        "2026/9/28",
        None,
        "2026-99-99",
        "2026-10-20T12:00:00",
    ]
    issues = [
        source.issues[0].model_copy(
            update={
                "no": i + 1,
                "source_row": i + 2,
                "status": status,
                "when": dates[i],
                "owner": None if i == 2 else "ME",
                "product": "P1" if i < 4 else "P2",
                "area": "L136" if i < 4 else "L166",
                "station": "LS1" if i % 2 else "LS2",
            }
        )
        for i, status in enumerate(statuses)
    ]
    result = source.model_copy(update={"issues": issues})
    store.import_result(result)
    return result


def test_report_counts_raw_distributions_sources_and_pending(store, source, tmp_path):
    text, result = generate_report(store, QueryFilters(), tmp_path / "reports")
    assert (
        result["total"],
        result["closed_count"],
        result["open_count"],
        result["uncertain_count"],
    ) == (7, 2, 2, 3)
    assert [x["issue_no"] for x in result["open_issues"]] == [3, 4]
    assert {x["issue_no"] for x in result["pending"]} == {2, 3, 5, 6, 7}
    for distribution in result["distributions"]:
        expected = Counter(getattr(issue, distribution["field"]) for issue in source.issues)
        assert {x["value"]: x["count"] for x in distribution["groups"]} == dict(expected)
        assert sum(x["count"] for x in distribution["groups"]) == 7
    assert "财年" in text and "年份未说明" in text and "日期不完整" in text
    assert "第 4 行" in text and "No.3" in text and source.source_file in text
    assert "done" in text and "+08:00" in result["generated_at"]
    from pathlib import Path

    assert Path(result["file_path"]).read_text(encoding="utf-8") == text


def test_scope_is_exact_and_report_reads_more_than_one_page(store, source, tmp_path):
    _, selected = generate_report(store, QueryFilters(area="L136"), tmp_path / "reports")
    assert selected["total"] == 4 and selected["filters"]["area"] == "L136"
    many = [
        source.issues[0].model_copy(update={"no": i, "source_row": i + 1}) for i in range(1, 106)
    ]
    store.import_result(source.model_copy(update={"issues": many}))
    _, all_records = generate_report(store, QueryFilters(), tmp_path / "reports")
    assert all_records["total"] == all_records["closed_count"] == 105
    assert all(
        sum(x["count"] for x in group["groups"]) == 105 for group in all_records["distributions"]
    )


def test_empty_scope_produces_honest_empty_report(store, tmp_path):
    text, result = generate_report(store, QueryFilters(area="missing"), tmp_path / "reports")
    assert result["total"] == 0 and result["pending"] == []
    assert "当前范围无记录" in text and "不代表全部问题已关闭" in text


def test_current_analysis_pending_used_but_stale_analysis_not_used(store, source, tmp_path):
    issue = source.issues[0]
    analysis = IssueAnalysis(
        title="问题",
        summary={
            "text": "原始问题",
            "evidence": [{"field_name": "problem_description", "quote": issue.problem_description}],
        },
        causes=[],
        actions=[],
        observations=[],
        verifications=[],
        missing_information=["验证结果待补充"],
        warnings=[],
    )
    result = IssueAnalysisResult(
        source_file=source.source_file,
        sheet_name=source.sheet_name,
        column_mapping=source.column_mapping,
        issue=issue,
        analysis=analysis,
        evidence_locations=locate_evidence(issue, analysis, source.column_mapping),
    )
    store.save_analysis(result, render_issue_card(result))
    text, _ = generate_report(store, QueryFilters(), tmp_path / "reports")
    assert "验证结果待补充" in text and "未经人工审核" in text
    store.import_result(
        source.model_copy(update={"issues": [issue.model_copy(update={"action": "new"})]})
    )
    text, _ = generate_report(store, QueryFilters(), tmp_path / "reports")
    assert "基于旧版记录" in text and "验证结果待补充" not in text


@pytest.mark.parametrize("value", ["2026-10-20", "2026/9/28", "20261020", "2026-10-20T12:00:00"])
def test_standard_dates_not_guessed_or_marked_uncertain(value):
    assert _date_note(value) is None


def test_agent_direct_report_not_rewritten_and_errors_sanitized(
    store, source, tmp_path, monkeypatch
):
    import feature.issue_report.tools as report_tools

    monkeypatch.setattr(
        report_tools, "generate_report", lambda s, f: generate_report(s, f, tmp_path / "reports")
    )

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "generate_issue_report",
                        "args": {"filters": {"area": "L136"}},
                        "id": "report",
                    }
                ],
            ),
            AIMessage(content="incorrect rewrite"),
        ]
    )
    with SqliteSaver.from_conn_string(":memory:") as saver:
        chat = ChatService(build_agent(model, saver, store))
        text = chat.reply("report", "生成 L136 报告")
        assert "问题总数：**4 条**" in text and model.i == 1
        assert chat.history("report")[-1].artifact["total"] == 4

    def fail(*_):
        raise RuntimeError("private-service-details")

    monkeypatch.setattr(report_tools, "generate_report", fail)
    error = report_tools.create_report_tools(store)[0].invoke({"filters": {}})
    assert "报告生成失败" in error and "private-service-details" not in error
