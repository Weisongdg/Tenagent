"""SQLModel 表声明；当前原始记录与每次整理结果分别保存。"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class IssueRecord(SQLModel, table=True):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("source_file", "sheet_name", "issue_no"),)

    id: int | None = Field(default=None, primary_key=True)
    source_file: str
    sheet_name: str
    issue_no: int = Field(index=True)
    product: str | None = Field(default=None, index=True)
    status: str | None = Field(default=None, index=True)
    raw_snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    column_mapping: dict = Field(sa_column=Column(JSON, nullable=False))
    fingerprint: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class AnalysisRecord(SQLModel, table=True):
    __tablename__ = "analyses"

    id: int | None = Field(default=None, primary_key=True)
    issue_id: int = Field(foreign_key="issues.id", index=True)
    fingerprint: str
    schema_version: int
    result_json: dict = Field(sa_column=Column(JSON, nullable=False))
    cell_types: dict = Field(sa_column=Column(JSON, nullable=False))
    card: str
    created_at: str = Field(default_factory=utc_now)
