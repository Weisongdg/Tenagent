"""薄数据访问层：复用 SQLModel 的引擎、Session、查询和事务。"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import URL, func
from sqlmodel import Session, SQLModel, create_engine, select

from .models import AnalysisRecord, IssueRecord


class IssueRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path.resolve())),
            connect_args={"check_same_thread": False},
        )
        try:
            SQLModel.metadata.create_all(self.engine)
        except Exception:
            self.engine.dispose()
            raise

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with Session(self.engine) as session, session.begin():
            yield session

    @staticmethod
    def find(session: Session, source_file: str, sheet_name: str, issue_no: int):
        return session.exec(
            select(IssueRecord).where(
                IssueRecord.source_file == source_file,
                IssueRecord.sheet_name == sheet_name,
                IssueRecord.issue_no == issue_no,
            )
        ).one_or_none()

    @staticmethod
    def list_records(
        session: Session, product: str | None, status: str | None, offset: int, limit: int
    ) -> tuple[int, list[IssueRecord]]:
        filters = []
        if product is not None:
            filters.append(IssueRecord.product == product)
        if status is not None:
            filters.append(IssueRecord.status == status)
        total = session.exec(select(func.count()).select_from(IssueRecord).where(*filters)).one()
        records = session.exec(
            select(IssueRecord).where(*filters).order_by(IssueRecord.id).offset(offset).limit(limit)
        ).all()
        return total, list(records)

    @staticmethod
    def analyses(session: Session, issue_id: int, limit: int) -> list[AnalysisRecord]:
        return list(
            session.exec(
                select(AnalysisRecord)
                .where(AnalysisRecord.issue_id == issue_id)
                .order_by(AnalysisRecord.id.desc())
                .limit(limit)
            ).all()
        )
