"""复用 SQLAlchemy JSON 运算符、匹配、COUNT 和 GROUP BY，只查询 issues 表。"""

from sqlalchemy import and_, false, func, or_
from sqlmodel import Session, select

from feature.issue_store.models import IssueRecord

from .schemas import GroupField, QueryFilters

SEARCH_FIELDS = ("problem_description", "root_cause", "action")


def raw_column(name: str):
    return IssueRecord.raw_snapshot["values"][name].as_string()


def build_conditions(filters: QueryFilters) -> list:
    conditions = []
    if filters.file_path is not None:
        conditions.append(IssueRecord.source_file == filters.file_path)
    if filters.sheet_name is not None:
        conditions.append(IssueRecord.sheet_name == filters.sheet_name)
    for field in ("product", "area", "station", "status"):
        value = getattr(filters, field)
        if value is not None:
            conditions.append(raw_column(field) == value)
    if filters.keywords:
        terms = [
            or_(*(raw_column(field).icontains(term, autoescape=True) for field in SEARCH_FIELDS))
            for term in filters.keywords
        ]
        conditions.append((or_ if filters.keyword_mode == "any" else and_)(*terms))
    return conditions


class QueryRepository:
    @staticmethod
    def count(session: Session, conditions: list) -> int:
        return session.exec(select(func.count()).select_from(IssueRecord).where(*conditions)).one()

    @staticmethod
    def search(session: Session, filters: QueryFilters, conditions: list, offset: int, limit: int):
        # 命中字段由数据库按同一套 LIKE 规则确定，避免在 Python 中另写匹配逻辑。
        field_matches = [
            or_(*(raw_column(field).icontains(term, autoescape=True) for term in filters.keywords))
            if filters.keywords
            else false()
            for field in SEARCH_FIELDS
        ]
        return session.exec(
            select(IssueRecord, *field_matches)
            .where(*conditions)
            .order_by(IssueRecord.id)
            .offset(offset)
            .limit(limit)
        ).all()

    @staticmethod
    def groups(session: Session, conditions: list, group_by: GroupField, offset: int, limit: int):
        field = raw_column(group_by)
        grouped = (
            select(field.label("value"), func.count().label("count"))
            .select_from(IssueRecord)
            .where(*conditions)
            .group_by(field)
        )
        total_groups = session.exec(select(func.count()).select_from(grouped.subquery())).one()
        rows = session.exec(
            grouped.order_by(func.count().desc(), field).offset(offset).limit(limit)
        ).all()
        return total_groups, rows
