"""查询参数和程序计算的结果；不接受模型提供数量或来源。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from feature.excel_reader import RawIssue

type GroupField = Literal["product", "area", "station", "status"]
type SearchField = Literal["problem_description", "root_cause", "action"]
type QueryText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: QueryText | None = Field(default=None, description="来源路径，省略表示全部入库来源")
    sheet_name: QueryText | None = None
    product: QueryText | None = None
    area: QueryText | None = None
    station: QueryText | None = None
    status: QueryText | None = Field(
        default=None, description="原始状态，例如 under tracking、Closed"
    )
    keywords: list[QueryText] = Field(default_factory=list, max_length=10)
    keyword_mode: Literal["any", "all"] = "any"

    @field_validator("file_path", "sheet_name", "product", "area", "station", "status")
    @classmethod
    def reject_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("筛选条件不能只有空白。")
        return value

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("检索词不能只有空白。")
        return list(dict.fromkeys(values))


class FieldMatch(BaseModel):
    field_name: SearchField
    cell: str
    text: str


class IssueHit(BaseModel):
    issue_id: int
    issue_no: int
    source_file: str
    sheet_name: str
    issue: RawIssue
    column_mapping: dict[str, str]
    matches: list[FieldMatch]


class SearchResult(BaseModel):
    filters: QueryFilters
    total: int
    offset: int
    limit: int
    next_offset: int | None
    issues: list[IssueHit]


class StatisticsGroup(BaseModel):
    value: str | int | float | None
    count: int
    examples: list[IssueHit]


class StatisticsResult(BaseModel):
    filters: QueryFilters
    group_by: GroupField
    total: int
    total_groups: int
    offset: int
    limit: int
    next_offset: int | None
    groups: list[StatisticsGroup]
