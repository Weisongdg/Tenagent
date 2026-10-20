"""使用 Jinja2 固定模板呈现已校验的数据，不调用模型或改变分类。"""

import json
import re

from jinja2 import Environment, PackageLoader, StrictUndefined

from .schemas import IssueAnalysisResult

ACTION_LABELS = {
    "completed": "原记录标明已完成",
    "in_progress": "进行中",
    "proposed": "仅提出",
    "unknown": "执行情况未说明",
}
CAUSE_LABELS = {
    "suspected": "疑似原因",
    "reported_confirmed": "原文明确认定，未经系统验证",
    "unknown": "确认程度未说明",
}
ISSUE_STATUS_LABELS = {
    "under tracking": "跟踪中",
    "open": "未关闭",
    "closed": "已关闭",
    "close": "已关闭",
    "done": "记录为完成",
}
VERIFICATION_LABELS = {
    "pending": "待验证",
    "reported_effective": "原记录报告措施有效",
    "reported_ineffective": "原记录报告措施无效",
    "unknown": "验证结果未说明",
}

_ENVIRONMENT = Environment(
    loader=PackageLoader("feature.issue_analysis", "templates"),
    undefined=StrictUndefined,
    autoescape=False,  # 输出 Markdown 文本，不做会改变原文的 HTML 转义。
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _display(value: object) -> str:
    if value is None:
        return "未填写"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render_issue_card(result: IssueAnalysisResult) -> str:
    """只精简展示；完整原始记录、状态、关联和引用仍由工具 artifact 保存。"""
    analysis = result.analysis
    raw_values = result.issue.model_dump(mode="json")
    context = " · ".join(
        _display(raw_values[field])
        for field in ("product", "area", "station")
        if raw_values[field] is not None and str(raw_values[field]).strip()
    )
    date_text = _display(raw_values["when"])
    if raw_values["when"] is None or not date_text.strip():
        date_text = "未说明"
    elif not re.search(r"(?<!\d)\d{4}(?!\d)", date_text):
        # 仅提示原值中未写出四位年份，不推断财年/日历年或修正错误日期。
        date_text += "，年份未说明"
    original_status = _display(raw_values["status"])
    status = ISSUE_STATUS_LABELS.get(original_status.strip().casefold(), original_status)

    problem_parts = [analysis.summary.text]
    for observation in analysis.observations:
        text = observation.text.rstrip("。")
        if observation.kind == "recovery" and not observation.action_links:
            text += "（尚无法对应到具体措施）"
        problem_parts.append(text)
    # 只去掉完全相同的文本，不自行做语义合并或改写。
    problem = "；".join(dict.fromkeys(part.rstrip("。") for part in problem_parts))
    causes = [
        f"{item.text.rstrip('。')}（{CAUSE_LABELS[item.status]}）" for item in analysis.causes
    ]
    actions = [
        (f"A{index + 1}：" if len(analysis.actions) > 1 else "")
        + f"{item.text.rstrip('。')}（{ACTION_LABELS[item.status]}）"
        for index, item in enumerate(analysis.actions)
    ]
    verifications = []
    for item in analysis.verifications:
        references = "、".join(f"A{link.action_index + 1}" for link in item.action_links)
        if references and len(analysis.actions) == 1:
            references = "本条措施"
        relation = f"关联：{references}" if references else "尚无法对应到具体措施"
        verifications.append(
            f"{item.text.rstrip('。')}（{VERIFICATION_LABELS[item.status]}；{relation}）"
        )
    cells = list(
        dict.fromkeys(
            location.cell
            for location in result.evidence_locations
            if location.field_name in ("problem_description", "root_cause", "action")
        )
    )
    return _ENVIRONMENT.get_template("issue_card.md.j2").render(
        result=result,
        analysis=analysis,
        context=context,
        date_text=date_text,
        owner=_display(raw_values["owner"]),
        status=status,
        problem=problem,
        causes=causes,
        actions=actions,
        verifications=verifications,
        cells=cells,
        missing_information=[item.rstrip("。") for item in analysis.missing_information],
        warnings=[item.text.rstrip("。") for item in analysis.warnings],
    )
