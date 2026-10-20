"""保留 Excel 单元格原值和来源，不在读取阶段清洗业务含义。"""

from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, Field, computed_field

type CellValue = str | int | float | bool | datetime | date | time | timedelta | None


class RawIssue(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source_row: int = Field(ge=2)
    no: CellValue
    product: CellValue
    when: CellValue
    area: CellValue
    station: CellValue
    problem_description: CellValue
    category: CellValue
    impact_qty: CellValue
    root_cause: CellValue
    owner: CellValue
    status: CellValue
    action: CellValue


class ExcelReadResult(BaseModel):
    source_file: str
    sheet_name: str
    column_mapping: dict[str, str]
    issues: list[RawIssue]

    @computed_field
    @property
    def issue_count(self) -> int:
        return len(self.issues)
