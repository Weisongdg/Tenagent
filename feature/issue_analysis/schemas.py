"""用 Pydantic 约束整理结果，原始记录和来源由程序附加。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feature.excel_reader import RawIssue

type SourceField = Literal[
    "no",
    "product",
    "when",
    "area",
    "station",
    "problem_description",
    "category",
    "impact_qty",
    "root_cause",
    "owner",
    "status",
    "action",
]


class AnalysisSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceQuote(AnalysisSchema):
    field_name: SourceField = Field(description="引用的原始记录字段名")
    quote: str = Field(min_length=1, description="该字段中连续、逐字一致的原文，不翻译或改写")


class Statement(AnalysisSchema):
    text: str = Field(min_length=1, description="有原文依据的中文表述，保留不确定性")
    evidence: list[EvidenceQuote] = Field(min_length=1, description="支持此表述的原文引用")


class Cause(Statement):
    status: Literal["suspected", "reported_confirmed", "unknown"] = Field(
        description="疑似 / 正文明确认定根因或说明已确认 / 确认程度未说明；仅位于根因字段不算确认"
    )


class Action(Statement):
    status: Literal["proposed", "in_progress", "completed", "unknown"] = Field(
        description="建议 / 进行中 / 原记录明确已完成 / 未说明执行情况"
    )


class ActionLink(AnalysisSchema):
    action_index: int = Field(
        ge=0, strict=True, description="关联措施在 actions 中的序号，从 0 开始"
    )
    evidence: list[EvidenceQuote] = Field(
        min_length=1, description="原文中明确支持措施和观察或验证之间关系的片段"
    )


class Observation(Statement):
    kind: Literal["inspection", "recovery", "unspecified"] = Field(
        description="检查观察 / 状态恢复 / 性质待确认；均不等于措施有效性验证"
    )
    action_links: list[ActionLink] = Field(description="原文明确关联的措施；无法对应时用 []")


class Verification(Statement):
    status: Literal["pending", "reported_effective", "reported_ineffective", "unknown"] = Field(
        description="待验证 / 原记录报告措施有效 / 原记录报告措施无效 / 结果未说明"
    )
    action_links: list[ActionLink] = Field(description="有原文关系依据的措施；无法对应时用 []")

    @model_validator(mode="after")
    def require_target_for_effectiveness(self) -> Self:
        if self.status in ("reported_effective", "reported_ineffective") and not self.action_links:
            raise ValueError("措施有效或无效结论必须有关联措施及关系依据。")
        return self


class IssueAnalysis(AnalysisSchema):
    """仅根据当前一条原始记录进行整理，不提出新的诊断或解决方案。"""

    title: str = Field(min_length=1, description="从问题摘要提炼的简短中文标题")
    summary: Statement = Field(description="问题现象的中文摘要及原文依据")
    causes: list[Cause] = Field(description="原因；没有依据时为空列表")
    actions: list[Action] = Field(description="逐项列出措施及各自执行状态，不合并不同阶段")
    observations: list[Observation] = Field(description="检查观察和状态恢复，不使用通过/失败标签")
    verifications: list[Verification] = Field(description="措施有效性验证；没有依据时为空列表")
    missing_information: list[str] = Field(description="缺失或无法确定的信息，中文说明")
    warnings: list[Statement] = Field(description="有原文依据的冲突或含糊信息；无则为空列表")

    @model_validator(mode="after")
    def check_action_references(self) -> Self:
        for item in [*self.observations, *self.verifications]:
            indices = [link.action_index for link in item.action_links]
            if len(indices) != len(set(indices)):
                raise ValueError("同一项观察或验证不能重复关联同一措施。")
            if any(index >= len(self.actions) for index in indices):
                raise ValueError("关联的措施序号不存在。")
        return self


class EvidenceLocation(EvidenceQuote):
    conclusion_path: str
    cell: str


class IssueAnalysisResult(AnalysisSchema):
    schema_version: Literal[2] = 2
    source_file: str
    sheet_name: str
    column_mapping: dict[str, str]
    issue: RawIssue
    analysis: IssueAnalysis
    evidence_locations: list[EvidenceLocation]
