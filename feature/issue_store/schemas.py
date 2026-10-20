"""问题库输入输出；日期类单元格借助 Pydantic 保留原始类型。"""

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

from feature.excel_reader import RawIssue
from feature.issue_analysis.schemas import IssueAnalysisResult

CELL_ADAPTERS = {cls.__name__: TypeAdapter(cls) for cls in (date, datetime, time, timedelta)}


class RawSnapshot(BaseModel):
    # JSON 本身无法区分日期和普通字符串，额外保存这些单元格的类型。
    values: dict
    cell_types: dict[str, Literal["date", "datetime", "time", "timedelta"]] = Field(
        default_factory=dict
    )

    @classmethod
    def from_issue(cls, issue: RawIssue) -> "RawSnapshot":
        return cls(
            values=issue.model_dump(mode="json"),
            cell_types={
                name: type(value).__name__
                for name, value in issue.model_dump().items()
                if type(value).__name__ in CELL_ADAPTERS
            },
        )

    def to_issue(self) -> RawIssue:
        values = dict(self.values)
        for name, kind in self.cell_types.items():
            values[name] = CELL_ADAPTERS[kind].validate_python(values[name])
        return RawIssue.model_validate(values)


class ImportResult(BaseModel):
    source_file: str
    sheet_name: str
    total: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


class IssueSummary(BaseModel):
    issue_id: int
    issue_no: int
    source_file: str
    sheet_name: str
    source_row: int
    product: str | None
    status: str | None
    updated_at: str


class IssuePage(BaseModel):
    total: int
    offset: int
    limit: int
    issues: list[IssueSummary]


class SavedAnalysis(BaseModel):
    analysis_id: int
    created_at: str
    schema_version: int
    stale: bool
    review_status: Literal["unreviewed"] = "unreviewed"
    card: str
    result: IssueAnalysisResult


class StoredIssue(BaseModel):
    issue_id: int
    source_file: str
    sheet_name: str
    issue_no: int
    issue: RawIssue
    cell_types: dict[str, str]
    column_mapping: dict[str, str]
    updated_at: str
    analyses: list[SavedAnalysis]
