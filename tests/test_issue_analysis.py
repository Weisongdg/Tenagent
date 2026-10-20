"""真实 LangChain 结构化解析和工具循环 + 假模型；不发起网络请求。"""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from feature.chat import ChatService, build_agent
from feature.excel_reader import RawIssue, read_issues
from feature.excel_reader.reader import DEFAULT_FILE
from feature.issue_analysis import IssueAnalysis, IssueAnalysisError, analyze_issue
from feature.issue_analysis.evidence import locate_evidence
from feature.issue_analysis.tools import create_analysis_tools
from tools import build_tools


class RecordingModel(FakeMessagesListChatModel):
    received: list[list[BaseMessage]] = Field(default_factory=list)
    bound_names: list[str] = Field(default_factory=list)
    fail: bool = False

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_names.extend(convert_to_openai_tool(item)["function"]["name"] for item in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        if self.fail:
            raise RuntimeError("private-provider-response")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def issue() -> RawIssue:
    return RawIssue(
        source_row=17,
        no=16,
        product="测试产品",
        when="July-11",
        area="CR106",
        station="ALL",
        problem_description="FFU shutdown during repair.",
        category=None,
        impact_qty="10pcs",
        root_cause="Suspected ATS-related issue",
        owner=None,
        status="done",
        action="Propose inspection. Samples sent, pending cleanliness result.",
    )


@pytest.fixture
def payload() -> dict:
    return {
        "title": "FFU 停机问题",
        "summary": {
            "text": "维修期间出现 FFU 停机。",
            "evidence": [{"field_name": "problem_description", "quote": "FFU shutdown"}],
        },
        "causes": [
            {
                "text": "疑似 ATS 相关问题。",
                "status": "suspected",
                "evidence": [{"field_name": "root_cause", "quote": "Suspected ATS-related issue"}],
            }
        ],
        "actions": [
            {
                "text": "建议检查。",
                "status": "proposed",
                "evidence": [{"field_name": "action", "quote": "Propose inspection"}],
            },
            {
                "text": "样品已送出。",
                "status": "completed",
                "evidence": [{"field_name": "action", "quote": "Samples sent"}],
            },
        ],
        "observations": [],
        "verifications": [
            {
                "text": "清洁度结果待返回。",
                "status": "pending",
                "action_links": [],
                "evidence": [{"field_name": "action", "quote": "pending cleanliness result"}],
            }
        ],
        "missing_information": ["负责人未说明", "发生日期缺少年份"],
        "warnings": [
            {
                "text": "整条状态为 done，但仍有待返回的验证结果，需确认。",
                "evidence": [
                    {"field_name": "status", "quote": "done"},
                    {"field_name": "action", "quote": "pending cleanliness result"},
                ],
            }
        ],
    }


def structured_message(payload: dict) -> AIMessage:
    return AIMessage(
        content="",
        id=str(uuid4()),
        tool_calls=[{"name": "IssueAnalysis", "args": payload, "id": str(uuid4())}],
    )


def test_structured_parser_retains_statuses_and_sends_only_one_issue(issue, payload):
    before = issue.model_dump()
    model = RecordingModel(responses=[structured_message(payload)])
    result = analyze_issue(issue, model)
    assert result.causes[0].status == "suspected"
    assert [action.status for action in result.actions] == ["proposed", "completed"]
    assert result.verifications[0].status == "pending"
    assert result.warnings[0].evidence[0].quote == "done"
    assert issue.model_dump() == before
    assert model.bound_names == ["IssueAnalysis"]
    assert len(model.received) == 1
    assert len(model.received[0]) == 2
    assert model.received[0][0].type == "system"
    assert model.received[0][1].text.endswith(issue.model_dump_json())


@pytest.mark.parametrize("case", ["invented_quote", "wrong_field", "blank_quote", "null_field"])
def test_invalid_source_quotes_are_rejected(issue, payload, case):
    evidence = payload["verifications"][0]["evidence"][0]
    if case == "invented_quote":
        evidence["quote"] = "cleanliness test passed"
    elif case == "wrong_field":
        evidence["field_name"] = "problem_description"
    elif case == "blank_quote":
        evidence["quote"] = " "
    else:
        evidence.update(field_name="owner", quote="null")
    model = RecordingModel(responses=[structured_message(payload)])
    with pytest.raises(IssueAnalysisError, match="原文引用校验失败"):
        analyze_issue(issue, model)
    assert len(model.received) == 1  # 校验失败不自动修复或重试。


@pytest.mark.parametrize(
    "case", ["bad_status", "missing_evidence", "invalid_field", "no_structure"]
)
def test_malformed_structured_output_is_not_a_success(issue, payload, case):
    if case == "bad_status":
        payload["verifications"][0]["status"] = "solved"
    elif case == "missing_evidence":
        payload["actions"][0]["evidence"] = []
    elif case == "invalid_field":
        payload["causes"][0]["evidence"][0]["field_name"] = "another_issue"
    response = (
        AIMessage(content="普通文字，未返回结构")
        if case == "no_structure"
        else structured_message(payload)
    )
    model = RecordingModel(responses=[response])
    with pytest.raises(IssueAnalysisError, match="未生成有效整理结果"):
        analyze_issue(issue, model)


def test_model_error_and_unsupported_output_have_clear_safe_failures(issue, payload):
    model = RecordingModel(responses=[structured_message(payload)], fail=True)
    with pytest.raises(IssueAnalysisError) as caught:
        analyze_issue(issue, model)
    assert "RuntimeError" in str(caught.value)
    assert "private-provider-response" not in str(caught.value)
    unsupported = FakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    with pytest.raises(IssueAnalysisError, match="NotImplementedError"):
        analyze_issue(issue, unsupported)


def test_cell_locations_use_actual_reader_mapping(issue, payload):
    analysis = IssueAnalysis.model_validate(payload)
    locations = locate_evidence(
        issue,
        analysis,
        {"problem_description": "D", "root_cause": "G", "action": "B", "status": "A"},
    )
    assert locations[0].cell == "D17"
    verification = next(item for item in locations if item.conclusion_path == "verifications[0]")
    assert verification.cell == "B17"


def test_missing_duplicate_numbers_and_file_errors_do_not_call_model(
    monkeypatch, payload, tmp_path
):
    import feature.issue_analysis.tools as analysis_tools

    model = RecordingModel(responses=[structured_message(payload)])
    tools = build_tools(model)
    assert [tool.name for tool in tools] == ["read_excel_issues", "analyze_excel_issue"]
    assert model.received == []
    tool = tools[1]
    assert "找不到" in tool.invoke({"issue_no": 999})
    assert "不存在" in tool.invoke({"issue_no": 1, "file_path": str(tmp_path / "missing.xlsx")})
    result = read_issues(DEFAULT_FILE)
    duplicate = result.model_copy(update={"issues": [result.issues[0], result.issues[0]]})
    monkeypatch.setattr(analysis_tools, "read_issues", lambda *_: duplicate)
    assert "重复记录" in tool.invoke({"issue_no": 1})
    assert model.received == []


def test_real_record_tool_includes_unchanged_input_and_verified_locations():
    record = read_issues(DEFAULT_FILE).issues[15]
    payload = {
        "title": "FFU 停机问题",
        "summary": {
            "text": "CR106 出现 FFU 停机。",
            "evidence": [{"field_name": "problem_description", "quote": "full FFU shutdown"}],
        },
        "causes": [],
        "actions": [],
        "observations": [
            {
                "text": "清洁度检测结果待返回。",
                "kind": "inspection",
                "action_links": [],
                "evidence": [{"field_name": "action", "quote": "pending cleanliness result"}],
            }
        ],
        "verifications": [],
        "missing_information": [],
        "warnings": [],
    }
    model = RecordingModel(responses=[structured_message(payload)])
    message = create_analysis_tools(model)[0].invoke(
        {
            "type": "tool_call",
            "name": "analyze_excel_issue",
            "args": {"issue_no": 16},
            "id": str(uuid4()),
        }
    )
    output = message.artifact
    assert "No.16｜" in message.content
    assert output["schema_version"] == 2
    assert output["issue"] == record.model_dump(mode="json")
    assert output["issue"]["status"] == "done"
    assert output["analysis"]["observations"][0]["kind"] == "inspection"
    assert output["analysis"]["verifications"] == []
    assert output["evidence_locations"][1]["cell"] == "L17"
    assert model.received[0][1].text.endswith(record.model_dump_json())


@pytest.mark.parametrize("bad_quote", [False, True])
@pytest.mark.parametrize("read_after", [False, True])
def test_agent_runs_analysis_tool_and_handles_evidence_failure(
    tmp_path: Path, bad_quote: bool, read_after: bool
):
    payload = {
        "title": "螺丝松动问题",
        "summary": {
            "text": "发现 LVCM 螺丝松动。",
            "evidence": [
                {
                    "field_name": "problem_description",
                    "quote": "invented quote" if bad_quote else "LVCM screw1 loose issue",
                }
            ],
        },
        "causes": [],
        "actions": [],
        "observations": [],
        "verifications": [],
        "missing_information": [],
        "warnings": [],
    }
    tool_calls = [{"name": "analyze_excel_issue", "args": {"issue_no": 1}, "id": str(uuid4())}]
    if read_after:
        tool_calls.append({"name": "read_excel_issues", "args": {}, "id": str(uuid4())})
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                id=str(uuid4()),
                tool_calls=tool_calls,
            ),
            structured_message(deepcopy(payload)),
            AIMessage(content="整理失败" if bad_quote else "整理完成", id=str(uuid4())),
        ]
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(model, saver))
        card = service.reply("analysis-test", "请整理编号 1 的问题")
        messages = service.history("analysis-test")
    assert [message.type for message in messages] == ["human", "ai", "tool"] + (
        ["tool"] if read_after else []
    )
    tool_result = messages[2]
    assert tool_result.status == ("error" if bad_quote else "success")
    if bad_quote:
        assert "原文引用校验失败" in tool_result.content
    else:
        result = tool_result.artifact
        assert result["issue"]["no"] == 1
        assert result["evidence_locations"][0]["cell"] == "F2"
    # 中间的结构化提取仅发送当前记录，不把外层聊天历史送进整理器。
    assert len(model.received) == 2
    assert "请整理编号 1 的问题" not in model.received[1][1].text
    assert card == tool_result.content
    assert "整理完成" not in card

    # 重建模型及连接后，固定卡片和结构化 artifact 都完整保留。
    next_model = RecordingModel(
        responses=[
            AIMessage(content="普通追问回复", id=str(uuid4())),
            AIMessage(content="另一会话回复", id=str(uuid4())),
        ]
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        service = ChatService(build_agent(next_model, saver))
        restored = next(
            message
            for message in service.history("analysis-test")
            if message.type == "tool" and message.name == "analyze_excel_issue"
        )
        assert restored.content == card
        assert restored.artifact == tool_result.artifact
        assert service.reply("analysis-test", "谢谢") == "普通追问回复"
        assert service.reply("another-session", "你好") == "另一会话回复"
        assert len(service.history("another-session")) == 2
