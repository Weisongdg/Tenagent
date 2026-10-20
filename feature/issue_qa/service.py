"""组合数据库查询及来源信息，不调用模型，不读取 Excel 或历史整理。"""

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from feature.issue_store import IssueStore
from feature.issue_store.schemas import RawSnapshot
from feature.issue_store.service import source_path

from .repository import SEARCH_FIELDS, QueryRepository, build_conditions, raw_column
from .schemas import (
    FieldMatch,
    GroupField,
    IssueHit,
    QueryFilters,
    SearchResult,
    StatisticsGroup,
    StatisticsResult,
)


class IssueQueryError(ValueError):
    """可展示的查询错误，不包含 SQL 或原始数据库参数。"""


def _prepare(filters: QueryFilters) -> QueryFilters:
    values = filters.model_dump()
    if filters.file_path is not None:
        values["file_path"] = source_path(filters.file_path)
    return QueryFilters.model_validate(values)


def _check_page(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise IssueQueryError("offset 必须是非负整数，limit 必须是 1～100 的整数。")


def _hit(row) -> IssueHit:
    record, *flags = row
    issue = RawSnapshot.model_validate(record.raw_snapshot).to_issue()
    values = issue.model_dump(mode="json")
    return IssueHit(
        issue_id=record.id,
        issue_no=record.issue_no,
        source_file=record.source_file,
        sheet_name=record.sheet_name,
        issue=issue,
        column_mapping=record.column_mapping,
        matches=[
            FieldMatch(
                field_name=field,
                cell=f"{record.column_mapping[field]}{issue.source_row}",
                text=str(values[field]),
            )
            for field, matched in zip(SEARCH_FIELDS, flags, strict=True)
            if matched
        ],
    )


class IssueQueryService:
    def __init__(self, store: IssueStore) -> None:
        self.store = store
        self.repository = QueryRepository()

    def search(self, filters: QueryFilters, offset: int = 0, limit: int = 10) -> SearchResult:
        _check_page(offset, limit)
        filters = _prepare(filters)
        try:
            with self.store.repository.transaction() as session:
                conditions = build_conditions(filters)
                total = self.repository.count(session, conditions)
                rows = self.repository.search(session, filters, conditions, offset, limit)
                return SearchResult(
                    filters=filters,
                    total=total,
                    offset=offset,
                    limit=limit,
                    next_offset=offset + limit if offset + limit < total else None,
                    issues=[_hit(row) for row in rows],
                )
        except SQLAlchemyError as error:
            raise IssueQueryError(
                "问题查询失败，请检查问题库是否可访问；未返回查询结果。"
            ) from error
        except (ValidationError, KeyError) as error:
            raise IssueQueryError("已存原始记录或来源信息不完整，未返回查询结果。") from error

    def summarize(
        self, filters: QueryFilters, group_by: GroupField, offset: int = 0, limit: int = 10
    ) -> StatisticsResult:
        _check_page(offset, limit)
        if group_by not in ("product", "area", "station", "status"):
            raise IssueQueryError("仅支持按 product、area、station、status 分组。")
        filters = _prepare(filters)
        try:
            with self.store.repository.transaction() as session:
                conditions = build_conditions(filters)
                total = self.repository.count(session, conditions)
                total_groups, rows = self.repository.groups(
                    session, conditions, group_by, offset, limit
                )
                groups = []
                for value, count in rows:
                    examples = self.repository.search(
                        session, filters, [*conditions, raw_column(group_by) == value], 0, 2
                    )
                    groups.append(
                        StatisticsGroup(
                            value=value,
                            count=count,
                            examples=[_hit(row) for row in examples],
                        )
                    )
                return StatisticsResult(
                    filters=filters,
                    group_by=group_by,
                    total=total,
                    total_groups=total_groups,
                    offset=offset,
                    limit=limit,
                    next_offset=offset + limit if offset + limit < total_groups else None,
                    groups=groups,
                )
        except SQLAlchemyError as error:
            raise IssueQueryError(
                "问题统计失败，请检查问题库是否可访问；未返回统计结果。"
            ) from error
        except (ValidationError, KeyError) as error:
            raise IssueQueryError("已存原始记录或来源信息不完整，未返回统计结果。") from error
