"""验证读取保真、模板错误和 Agent 的实际工具调用。"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from openpyxl import Workbook
from pydantic import Field

from config import PROJECT_DIR
from feature.chat import ChatService, build_agent
from feature.excel_reader import ExcelReadError, read_issues
from feature.excel_reader.reader import DEFAULT_FILE, DEFAULT_SHEET, HEADER_FIELDS
from feature.excel_reader.tools import read_excel_issues
from tools import build_tools


def write_fixture(path: Path, headers: list, rows: list[list]) -> Path:
    """只生成临时测试输入，不写用户工作簿。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = DEFAULT_SHEET
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def test_real_workbook_preserves_all_rows_values_and_source():
    source = PROJECT_DIR / DEFAULT_FILE
    before = hashlib.sha256(source.read_bytes()).digest()
    result = read_issues(source)

    assert result.issue_count == 21
    assert [issue.source_row for issue in result.issues] == list(range(2, 23))
    assert [issue.no for issue in result.issues] == list(range(1, 22))
    assert result.source_file == str(source.resolve())
    assert result.column_mapping["action"] == "L"
    assert result.column_mapping["root_cause"] == "I"
    first, last = result.issues[0], result.issues[-1]
    assert first.product == "Skybolt"
    assert first.impact_qty == "2856"  # 原文件存为文本，不强转数字。
    assert first.when == "Oct-30 FY2025"
    assert first.problem_description.endswith(" ")
    assert last.station == "CSI226"
    assert last.status == "Close"
    assert result.issues[1].area is None
    assert result.issues[14].when == "206/7/5"
    assert "\n" in result.issues[15].impact_qty
    assert "pending cleanliness result" in result.issues[15].action
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_headers_can_move_and_blank_rows_keep_physical_row_numbers(tmp_path: Path):
    headers = list(reversed(HEADER_FIELDS))
    row = [None] * len(headers)
    row[headers.index("No")] = 7
    row[headers.index("Prod")] = " 原文不去空白 "
    row[headers.index("Action")] = "措施一\n措施二"
    display_headers = [f"  {header.upper()}  " for header in headers]
    source = write_fixture(tmp_path / "moved.xlsx", display_headers, [row, [], row])
    result = read_issues(source)
    assert result.issue_count == 2
    assert [issue.source_row for issue in result.issues] == [2, 4]
    assert result.issues[0].product == " 原文不去空白 "
    assert result.issues[0].action == "措施一\n措施二"
    assert result.issues[0].category is None
    assert result.column_mapping["action"] == "A"


def test_native_types_and_formulas_are_preserved(tmp_path: Path):
    date_value = datetime(2026, 10, 19)
    source = write_fixture(
        tmp_path / "typed.xlsx",
        list(HEADER_FIELDS),
        [[0, "Prod", date_value, None, None, "问题", False, 1.5, None, None, None, "=1+2"]],
    )
    result = read_issues(source)
    issue = result.issues[0]
    assert type(issue.no) is int and issue.no == 0
    assert issue.when == date_value
    assert issue.category is False
    assert issue.impact_qty == 1.5
    assert issue.action == "=1+2"
    assert result.model_dump(mode="json")["issues"][0]["when"] == "2026-10-19T00:00:00"


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "no_header"])
def test_invalid_headers_are_explicit_errors(tmp_path: Path, kind: str):
    headers = list(HEADER_FIELDS)
    if kind == "missing":
        headers.remove("Owner")
    elif kind == "duplicate":
        headers.append(" NO ")
    elif kind == "unknown":
        headers.append("Unknown column")
    else:
        headers = []
    source = write_fixture(tmp_path / "invalid.xlsx", headers, [])
    with pytest.raises(ExcelReadError, match="表头"):
        read_issues(source)


def test_data_under_blank_header_is_not_silently_dropped(tmp_path: Path):
    source = write_fixture(
        tmp_path / "unnamed.xlsx", list(HEADER_FIELDS), [[None] * 12 + ["不能丢失的数据"]]
    )
    with pytest.raises(ExcelReadError, match="第 2 行存在没有表头"):
        read_issues(source)


def test_empty_sheet_with_valid_headers_returns_zero_records(tmp_path: Path):
    source = write_fixture(tmp_path / "empty.xlsx", list(HEADER_FIELDS), [])
    assert read_issues(source).issue_count == 0


def test_missing_file_sheet_wrong_format_and_corrupt_workbook(tmp_path: Path):
    with pytest.raises(ExcelReadError, match="不存在"):
        read_issues(tmp_path / "missing.xlsx")
    with pytest.raises(ExcelReadError, match="工作表不存在"):
        read_issues(PROJECT_DIR / DEFAULT_FILE, "不存在的表")
    wrong_format = tmp_path / "old.xls"
    wrong_format.write_text("test", encoding="utf-8")
    with pytest.raises(ExcelReadError, match="只支持 .xlsx"):
        read_issues(wrong_format)
    corrupt = tmp_path / "broken.xlsx"
    corrupt.write_bytes(b"not an Excel workbook")
    with pytest.raises(ExcelReadError, match="无法读取 Excel"):
        read_issues(corrupt)


def test_tool_registry_serialization_and_error_handling(tmp_path: Path):
    model = FakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    assert read_excel_issues in build_tools(model)
    assert read_excel_issues.invoke({}) == read_issues(DEFAULT_FILE).model_dump(mode="json")
    error = read_excel_issues.invoke({"file_path": str(tmp_path / "missing.xlsx")})
    assert "不存在" in error


def test_reading_is_independent_of_working_directory_and_model_config(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    result = read_issues(DEFAULT_FILE)
    assert result.issue_count == 21
    assert result.issues[0].source_row == 2
    assert not (tmp_path / "data").exists()


class ToolCallingModel(FakeMessagesListChatModel):
    """仅模拟模型的工具选择，工具和 Agent 循环使用真实实现。"""

    bound_names: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_names = [tool.name for tool in tools]
        return self


@pytest.mark.parametrize("missing_file", [False, True])
def test_agent_executes_registered_excel_tool(tmp_path: Path, missing_file: bool):
    arguments = {"file_path": str(tmp_path / "missing.xlsx")} if missing_file else {}
    model = ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                id=str(uuid4()),
                tool_calls=[{"name": "read_excel_issues", "args": arguments, "id": str(uuid4())}],
            ),
            AIMessage(content="读取完成" if not missing_file else "文件不存在", id=str(uuid4())),
        ]
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        service.reply("excel-test", "请读取问题追踪表")
        messages = service.history("excel-test")
    assert "read_excel_issues" in model.bound_names
    assert [message.type for message in messages] == ["human", "ai", "tool", "ai"]
    tool_result = messages[2]
    if missing_file:
        assert tool_result.status == "error"
        assert "不存在" in tool_result.content
    else:
        assert tool_result.status == "success"
        assert json.loads(tool_result.content)["issue_count"] == 21
