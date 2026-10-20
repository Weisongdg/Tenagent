"""问题库业务：幂等导入、来源指纹和整理历史；不调用模型。"""

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from config import PROJECT_DIR
from feature.excel_reader import ExcelReadResult, RawIssue, read_issues
from feature.excel_reader.reader import DEFAULT_FILE, DEFAULT_SHEET
from feature.issue_analysis.evidence import locate_evidence, validate_evidence
from feature.issue_analysis.renderer import render_issue_card
from feature.issue_analysis.schemas import IssueAnalysisResult

from .models import AnalysisRecord, IssueRecord, utc_now
from .repository import IssueRepository
from .schemas import ImportResult, IssuePage, IssueSummary, RawSnapshot, SavedAnalysis, StoredIssue


class IssueStoreError(ValueError):
    """可直接展示的存储错误，不包含数据库语句或原始参数。"""


def source_path(file_path: str | Path) -> str:
    path = Path(file_path).expanduser()
    return str((path if path.is_absolute() else PROJECT_DIR / path).resolve())


def issue_number(value) -> int:
    # 数字 6、6.0 与文本 "6" 对应同一 No；保留原始值，不修改源记录。
    if type(value) is int and value > 0:
        return value
    if type(value) is float and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise IssueStoreError("问题 No 必须是正整数或正整数文本；未导入任何记录。")


def fingerprint(issue: RawIssue, mapping: dict[str, str]) -> str:
    payload = {"raw": RawSnapshot.from_issue(issue).model_dump(), "columns": mapping}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class IssueStore:
    def __init__(self, path: Path) -> None:
        self.repository = IssueRepository(path)

    def __enter__(self) -> "IssueStore":
        return self

    def __exit__(self, *_) -> None:
        self.repository.close()

    def import_excel(
        self, file_path: str | Path = DEFAULT_FILE, sheet_name: str = DEFAULT_SHEET
    ) -> ImportResult:
        return self.import_result(read_issues(file_path, sheet_name))

    def import_result(self, source: ExcelReadResult) -> ImportResult:
        numbers = [issue_number(issue.no) for issue in source.issues]
        if len(numbers) != len(set(numbers)):
            raise IssueStoreError("同一工作表存在重复 No；未导入任何记录。")
        path = source_path(source.source_file)
        result = ImportResult(source_file=path, sheet_name=source.sheet_name, total=len(numbers))
        try:
            with self.repository.transaction() as session:
                for number, issue in zip(numbers, source.issues, strict=True):
                    _, change = self._upsert(
                        session, path, source.sheet_name, number, issue, source.column_mapping
                    )
                    setattr(result, change, getattr(result, change) + 1)
        except SQLAlchemyError as error:
            raise IssueStoreError(
                "问题库导入失败，已回滚本次导入；请检查数据库路径及权限。"
            ) from error
        return result

    def _upsert(
        self,
        session: Session,
        path: str,
        sheet: str,
        number: int,
        issue: RawIssue,
        mapping: dict[str, str],
    ) -> tuple[IssueRecord, str]:
        record = self.repository.find(session, path, sheet, number)
        digest = fingerprint(issue, mapping)
        if record is not None and record.fingerprint == digest:
            return record, "skipped"
        change = "inserted" if record is None else "updated"
        if record is None:
            record = IssueRecord(
                source_file=path,
                sheet_name=sheet,
                issue_no=number,
                raw_snapshot={},
                column_mapping={},
                fingerprint=digest,
            )
        record.raw_snapshot = RawSnapshot.from_issue(issue).model_dump()
        record.column_mapping = dict(mapping)
        record.fingerprint = digest
        record.product = None if issue.product is None else str(issue.product)
        record.status = None if issue.status is None else str(issue.status)
        record.updated_at = utc_now()
        session.add(record)
        session.flush()
        return record, change

    def current_fingerprint(
        self, issue_no: int, file_path: str | Path, sheet_name: str
    ) -> str | None:
        """在调用模型前记住库中版本，避免较早启动的整理覆盖较新的导入。"""
        try:
            with self.repository.transaction() as session:
                record = self.repository.find(
                    session, source_path(file_path), sheet_name, issue_number(issue_no)
                )
                return record.fingerprint if record is not None else None
        except SQLAlchemyError as error:
            raise IssueStoreError("无法读取问题库版本，未开始整理。") from error

    def save_analysis(
        self, result: IssueAnalysisResult, card: str, expected_fingerprint: str | None = None
    ) -> int:
        # 保存前沿用整理器的证据检查；卡片必须来自同一个固定渲染器。
        validate_evidence(result.issue, result.analysis)
        if result.evidence_locations != locate_evidence(
            result.issue, result.analysis, result.column_mapping
        ) or card != render_issue_card(result):
            raise IssueStoreError("整理结果的来源或卡片不一致，未保存。")
        try:
            with self.repository.transaction() as session:
                existing = self.repository.find(
                    session,
                    source_path(result.source_file),
                    result.sheet_name,
                    issue_number(result.issue.no),
                )
                if (
                    existing is not None
                    and existing.fingerprint != fingerprint(result.issue, result.column_mapping)
                    and existing.fingerprint != expected_fingerprint
                ):
                    raise IssueStoreError("问题库记录已变化，本次整理未入库，请重新整理。")
                record, _ = self._upsert(
                    session,
                    source_path(result.source_file),
                    result.sheet_name,
                    issue_number(result.issue.no),
                    result.issue,
                    result.column_mapping,
                )
                saved = AnalysisRecord(
                    issue_id=record.id,
                    fingerprint=record.fingerprint,
                    schema_version=result.schema_version,
                    result_json=result.model_dump(mode="json"),
                    cell_types=RawSnapshot.from_issue(result.issue).cell_types,
                    card=card,
                )
                session.add(saved)
                session.flush()
                analysis_id = saved.id
            return analysis_id
        except SQLAlchemyError as error:
            raise IssueStoreError("整理结果保存失败，已回滚；本次结果未入库。") from error

    def list_issues(
        self,
        product: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> IssuePage:
        if offset < 0 or not 1 <= limit <= 100:
            raise IssueStoreError("offset 必须非负，limit 必须在 1～100 之间。")
        try:
            with self.repository.transaction() as session:
                total, records = self.repository.list_records(
                    session, product, status, offset, limit
                )
                return IssuePage(
                    total=total,
                    offset=offset,
                    limit=limit,
                    issues=[
                        IssueSummary(
                            issue_id=record.id,
                            issue_no=record.issue_no,
                            source_file=record.source_file,
                            sheet_name=record.sheet_name,
                            source_row=record.raw_snapshot["values"]["source_row"],
                            product=record.product,
                            status=record.status,
                            updated_at=record.updated_at,
                        )
                        for record in records
                    ],
                )
        except SQLAlchemyError as error:
            raise IssueStoreError("问题库查询失败，请检查数据库是否可访问。") from error

    def get_issue(
        self,
        issue_no: int,
        file_path: str | Path = DEFAULT_FILE,
        sheet_name: str = DEFAULT_SHEET,
        history_limit: int = 1,
    ) -> StoredIssue:
        if not 1 <= history_limit <= 100:
            raise IssueStoreError("history_limit 必须在 1～100 之间。")
        try:
            with self.repository.transaction() as session:
                record = self.repository.find(
                    session, source_path(file_path), sheet_name, issue_number(issue_no)
                )
                if record is None:
                    raise IssueStoreError(f"问题库中找不到 No={issue_no}，请先导入该来源。")
                raw = RawSnapshot.model_validate(record.raw_snapshot)
                analyses = []
                for saved in self.repository.analyses(session, record.id, history_limit):
                    payload = dict(saved.result_json)
                    payload["issue"] = RawSnapshot(
                        values=payload["issue"], cell_types=saved.cell_types
                    ).to_issue()
                    analyses.append(
                        SavedAnalysis(
                            analysis_id=saved.id,
                            created_at=saved.created_at,
                            schema_version=saved.schema_version,
                            stale=saved.fingerprint != record.fingerprint,
                            card=saved.card,
                            result=IssueAnalysisResult.model_validate(payload),
                        )
                    )
                return StoredIssue(
                    issue_id=record.id,
                    source_file=record.source_file,
                    sheet_name=record.sheet_name,
                    issue_no=record.issue_no,
                    issue=raw.to_issue(),
                    cell_types=raw.cell_types,
                    column_mapping=record.column_mapping,
                    updated_at=record.updated_at,
                    analyses=analyses,
                )
        except SQLAlchemyError as error:
            raise IssueStoreError("问题库查询失败，请检查数据库是否可访问。") from error
        except (ValidationError, KeyError) as error:
            raise IssueStoreError("已保存的数据格式不兼容或不完整，未返回卡片。") from error
