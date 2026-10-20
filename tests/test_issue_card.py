"""围绕 No.13 的错误样例检查固定展示、观察分类和关联约束。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from feature.excel_reader import read_issues
from feature.excel_reader.reader import DEFAULT_FILE
from feature.issue_analysis.evidence import locate_evidence, validate_evidence
from feature.issue_analysis.renderer import render_issue_card
from feature.issue_analysis.schemas import IssueAnalysis, IssueAnalysisResult


@pytest.fixture
def result() -> IssueAnalysisResult:
    source = read_issues(DEFAULT_FILE)
    issue = next(item for item in source.issues if item.no == 13)
    analysis = IssueAnalysis.model_validate(
        {
            "title": "差压报警",
            "summary": {
                "text": "记录称发生差压报警。",
                "evidence": [
                    {"field_name": "problem_description", "quote": "a differential pressure alarm"}
                ],
            },
            "causes": [],
            "actions": [
                {
                    "text": "即时响应。",
                    "status": "completed",
                    "evidence": [
                        {
                            "field_name": "action",
                            "quote": (
                                "Reaction after DP between CR1/CR2 to "
                                "WASHING ROOM/SMOCK ROOM - Closed"
                            ),
                        }
                    ],
                },
                {
                    "text": "调整 AHU 参数。",
                    "status": "unknown",
                    "evidence": [
                        {
                            "field_name": "action",
                            "quote": (
                                "Adjust AHU frequency/air inlet valve/air return valve "
                                "based on feedback of DP data"
                            ),
                        }
                    ],
                },
                {
                    "text": "建设差压报警系统。",
                    "status": "proposed",
                    "evidence": [{"field_name": "action", "quote": "DP auto alert system"}],
                },
            ],
            "observations": [
                {
                    "text": "检查报告无异常。",
                    "kind": "inspection",
                    "action_links": [],
                    "evidence": [{"field_name": "action", "quote": "no abnormal"}],
                },
                {
                    "text": "FFU 运行正常。",
                    "kind": "inspection",
                    "action_links": [],
                    "evidence": [{"field_name": "action", "quote": "FFU normal running"}],
                },
                {
                    "text": "差压在 30 分钟内恢复正常。",
                    "kind": "recovery",
                    "action_links": [],
                    "evidence": [
                        {"field_name": "action", "quote": "back to normal within 30 mins"}
                    ],
                },
            ],
            "verifications": [],
            "missing_information": ["未说明 MAU 皮带的修复措施。"],
            "warnings": [],
        }
    )
    validate_evidence(issue, analysis)
    return IssueAnalysisResult(
        source_file=source.source_file,
        sheet_name=source.sheet_name,
        column_mapping=source.column_mapping,
        issue=issue,
        analysis=analysis,
        evidence_locations=locate_evidence(issue, analysis, source.column_mapping),
    )


def test_compact_card_keeps_each_action_status(result):
    card = render_issue_card(result)
    actions = next(line for line in card.splitlines() if line.startswith("**处理措施：**"))
    assert "A1：即时响应（原记录标明已完成）" in actions
    assert "A2：调整 AHU 参数（执行情况未说明）" in actions
    assert "A3：建设差压报警系统（仅提出）" in actions
    assert "###" not in card


def test_observations_stay_separate_and_unlinked_recovery_is_explicit(result):
    card = render_issue_card(result)
    problem = next(line for line in card.splitlines() if line.startswith("**问题：**"))
    assert "检查报告无异常" in problem
    assert "FFU 运行正常" in problem
    assert "30 分钟内恢复正常" in problem
    assert "尚无法对应到具体措施" in problem
    assert "通过" not in problem
    assert "原记录报告措施有效" not in card
    assert "当前记录未提供针对措施的有效性验证" in card


def test_compact_display_keeps_raw_data_and_evidence_in_result(result):
    before = result.model_dump(mode="json")
    card = render_issue_card(result)
    assert "**问题状态：**跟踪中" in card
    assert "**负责人：**FAC" in card
    assert "**来源：**第 14 行，主要依据 F14、L14。" in card
    assert "完整原始记录" not in card and "原文（" not in card
    assert result.issue.action not in card
    assert "CIMBP - 5936pcs" in result.issue.action
    assert result.issue.status == "under tracking"
    assert result.model_dump(mode="json") == before


def test_packaged_template_does_not_depend_on_working_directory(result, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "No.13｜" in render_issue_card(result)


@pytest.mark.parametrize(
    "when,has_year",
    [
        ("May-05", False),
        ("2026/9/28", True),
        ("Oct-30 FY2025", True),
    ],
)
def test_date_preserves_original_and_only_flags_missing_year(result, when, has_year):
    updated = result.model_copy(update={"issue": result.issue.model_copy(update={"when": when})})
    card = render_issue_card(updated)
    date_line = next(line for line in card.splitlines() if line.startswith("**日期：**"))
    assert when in date_line
    assert ("年份未说明" in date_line) is not has_year
    assert updated.issue.when == when


def test_no6_compact_card_shows_unknown_cause_and_translated_action():
    source = read_issues(DEFAULT_FILE)
    issue = next(item for item in source.issues if item.no == 6)
    analysis = IssueAnalysis.model_validate(
        {
            "title": "钢珠掉落",
            "summary": {
                "text": "钢珠掉落。",
                "evidence": [{"field_name": "problem_description", "quote": "钢珠掉落"}],
            },
            "causes": [
                {
                    "text": "记录提及 UVI 软件 Bug。",
                    "status": "unknown",
                    "evidence": [
                        {"field_name": "root_cause", "quote": "UVI sw bug with bias early return"}
                    ],
                }
            ],
            "actions": [
                {
                    "text": "切换回旧版软件。",
                    "status": "unknown",
                    "evidence": [{"field_name": "action", "quote": "change to old sw"}],
                }
            ],
            "observations": [
                {
                    "text": "记录另提到重复事件及两颗钢珠下落不明。",
                    "kind": "unspecified",
                    "action_links": [],
                    "evidence": [
                        {
                            "field_name": "root_cause",
                            "quote": "2x steel ball missing ,donot know where they are",
                        }
                    ],
                }
            ],
            "verifications": [],
            "warnings": [],
            "missing_information": ["是否已切换软件", "切换后是否再次发生", "钢珠追查结果"],
        }
    )
    validate_evidence(issue, analysis)
    output = IssueAnalysisResult(
        source_file=source.source_file,
        sheet_name=source.sheet_name,
        column_mapping=source.column_mapping,
        issue=issue,
        analysis=analysis,
        evidence_locations=locate_evidence(issue, analysis, source.column_mapping),
    )
    card = render_issue_card(output)
    assert "**No.6｜Summit · L136 · UVI 钢珠掉落**" in card
    assert "**日期：**May-05，年份未说明" in card
    assert "确认程度未说明" in card and "原文明确认定" not in card
    assert "切换回旧版软件（执行情况未说明）" in card
    assert "第 7 行，主要依据 F7、I7、L7" in card
    assert len(card.splitlines()) <= 12
    assert output.issue.action == "change to old sw"
    assert output.evidence_locations[1].quote == "UVI sw bug with bias early return"


@pytest.mark.parametrize("index", [-1, 3, True])
def test_invalid_action_references_are_rejected(result, index):
    payload = result.analysis.model_dump()
    payload["observations"][0]["action_links"] = [
        {"action_index": index, "evidence": [{"field_name": "action", "quote": "no abnormal"}]}
    ]
    with pytest.raises(ValidationError):
        IssueAnalysis.model_validate(payload)


def test_duplicate_reference_and_empty_relation_evidence_are_rejected(result):
    payload = result.analysis.model_dump()
    link = {"action_index": 0, "evidence": [{"field_name": "action", "quote": "no abnormal"}]}
    payload["observations"][0]["action_links"] = [link, deepcopy(link)]
    with pytest.raises(ValidationError, match="不能重复关联"):
        IssueAnalysis.model_validate(payload)
    payload["observations"][0]["action_links"] = [{"action_index": 0, "evidence": []}]
    with pytest.raises(ValidationError):
        IssueAnalysis.model_validate(payload)


@pytest.mark.parametrize("status", ["reported_effective", "reported_ineffective"])
def test_effectiveness_requires_explicit_target(result, status):
    payload = result.analysis.model_dump()
    payload["verifications"] = [
        {
            "text": "验证结论。",
            "status": status,
            "action_links": [],
            "evidence": [{"field_name": "action", "quote": "no abnormal"}],
        }
    ]
    with pytest.raises(ValidationError, match="必须有关联措施"):
        IssueAnalysis.model_validate(payload)


def test_unknown_verification_can_remain_unlinked(result):
    payload = result.analysis.model_dump()
    payload["verifications"] = [
        {
            "text": "验证结果未说明。",
            "status": "unknown",
            "action_links": [],
            "evidence": [{"field_name": "status", "quote": "under tracking"}],
        }
    ]
    analysis = IssueAnalysis.model_validate(payload)
    assert analysis.verifications[0].action_links == []


def test_relation_quote_is_validated_and_action_reference_is_preserved(result):
    # 构造仅在内存中的明确关系，测试引用校验及精简展示中的 A2 对应关系。
    relation = "After AHU adjustment, DP stayed stable in the verification test."
    issue = result.issue.model_copy(update={"action": result.issue.action + "\n" + relation})
    payload = result.analysis.model_dump()
    payload["verifications"] = [
        {
            "text": "原记录报告 AHU 调整后差压稳定。",
            "status": "reported_effective",
            "evidence": [{"field_name": "action", "quote": "DP stayed stable"}],
            "action_links": [
                {"action_index": 1, "evidence": [{"field_name": "action", "quote": relation}]}
            ],
        }
    ]
    analysis = IssueAnalysis.model_validate(payload)
    validate_evidence(issue, analysis)
    updated = IssueAnalysisResult(
        source_file=result.source_file,
        sheet_name=result.sheet_name,
        column_mapping=result.column_mapping,
        issue=issue,
        analysis=analysis,
        evidence_locations=locate_evidence(issue, analysis, result.column_mapping),
    )
    assert "关联：A2" in render_issue_card(updated)
    assert any(
        item.conclusion_path == "verifications[0].action_links[0]" and item.cell == "L14"
        for item in updated.evidence_locations
    )
    payload["verifications"][0]["action_links"][0]["evidence"][0]["quote"] = "invented relation"
    with pytest.raises(ValueError, match="action_links"):
        validate_evidence(issue, IssueAnalysis.model_validate(payload))
