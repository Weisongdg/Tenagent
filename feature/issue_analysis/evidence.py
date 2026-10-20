"""验证原文引用并计算单元格地址，不判断结论的语义是否正确。"""

import json
from collections.abc import Iterator

from feature.excel_reader import RawIssue

from .schemas import EvidenceLocation, EvidenceQuote, IssueAnalysis


def _quotes(analysis: IssueAnalysis) -> Iterator[tuple[str, EvidenceQuote]]:
    statements = [("summary", analysis.summary)]
    for name in ("causes", "actions", "observations", "verifications", "warnings"):
        statements.extend(
            (f"{name}[{index}]", statement)
            for index, statement in enumerate(getattr(analysis, name))
        )
    for path, statement in statements:
        for quote in statement.evidence:
            yield path, quote
        for index, link in enumerate(getattr(statement, "action_links", [])):
            for quote in link.evidence:
                yield f"{path}.action_links[{index}]", quote


def validate_evidence(issue: RawIssue, analysis: IssueAnalysis) -> None:
    """每个引用必须来自本条记录中指定字段的连续原文。"""
    values = issue.model_dump(mode="json")
    for path, evidence in _quotes(analysis):
        value = values[evidence.field_name]
        source = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if value is None or not evidence.quote.strip() or evidence.quote not in source:
            raise ValueError(f"{path} 引用的文字不存在于原始字段 {evidence.field_name} 中。")


def locate_evidence(
    issue: RawIssue, analysis: IssueAnalysis, column_mapping: dict[str, str]
) -> list[EvidenceLocation]:
    """模型不提供单元格地址；使用读取器提供的实际列和行号计算。"""
    return [
        EvidenceLocation(
            conclusion_path=path,
            field_name=evidence.field_name,
            quote=evidence.quote,
            cell=f"{column_mapping[evidence.field_name]}{issue.source_row}",
        )
        for path, evidence in _quotes(analysis)
    ]
