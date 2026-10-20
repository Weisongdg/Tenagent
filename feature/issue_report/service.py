"""复用问题库筛选、SQL 分组和 Jinja2，生成并保存范围报告。"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from sqlmodel import select

from config import PROJECT_DIR
from feature.issue_qa.renderer import FIELD_LABELS, display
from feature.issue_qa.repository import QueryRepository, build_conditions
from feature.issue_qa.schemas import QueryFilters
from feature.issue_store import IssueStore
from feature.issue_store.models import IssueRecord
from feature.issue_store.service import source_path


def _date_note(value) -> str | None:
    if value is None or not str(value).strip():
        return "发生日期未填写。"
    text = str(value)
    if "fy" in text.casefold():
        return "日期含财年表述，未转换为日历日期。"
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            datetime.strptime(text, pattern)
            return None
        except ValueError:
            pass
    try:
        datetime.fromisoformat(text)
        return None
    except ValueError:
        if not re.search(r"(?<!\d)\d{4}(?!\d)", text):
            return "日期年份未说明，不能确定完整发生日期。"
        return "日期不完整或格式未确定，未转换或计算超期。"


def generate_report(
    store: IssueStore, filters: QueryFilters, output_dir: Path = PROJECT_DIR / "data/reports"
) -> tuple[str, dict]:
    if filters.file_path is not None:
        filters = filters.model_copy(update={"file_path": source_path(filters.file_path)})
    with store.repository.transaction() as session:
        conditions = build_conditions(filters)
        records = session.exec(
            select(IssueRecord).where(*conditions).order_by(IssueRecord.id)
        ).all()
        # 读取完整结果，不使用查询工具的默认第一页或向量 Top k。
        items = [
            dict(
                issue_no=record.issue_no,
                source_file=record.source_file,
                sheet_name=record.sheet_name,
                raw=record.raw_snapshot["values"],
            )
            for record in records
        ]
        distributions = []
        for field in ("product", "area", "station", "status"):
            _, groups = QueryRepository.groups(session, conditions, field, 0, max(len(items), 1))
            if sum(count for _, count in groups) != len(items):
                raise ValueError("报告生成期间数据发生变化，请重新生成。")
            distributions.append(
                {
                    "field": field,
                    "label": FIELD_LABELS[field],
                    "groups": [{"value": value, "count": count} for value, count in groups],
                }
            )
    opened, pending, closed_count, uncertain_count = [], [], 0, 0
    for item in items:
        raw = item["raw"]
        status = str(raw["status"] if raw["status"] is not None else "").strip().casefold()
        notes = []
        if status in ("closed", "close"):
            closed_count += 1
        elif status in ("open", "under tracking"):
            opened.append(item)
        else:
            uncertain_count += 1
            notes.append("是否关闭待确认；done、空值及其他状态不自动视为已关闭。")
        if note := _date_note(raw["when"]):
            notes.append(note)
        for field, label in (
            ("owner", "负责人"),
            ("product", "产品"),
            ("area", "区域"),
            ("station", "工位"),
        ):
            if raw[field] is None or not str(raw[field]).strip():
                notes.append(f"{label}未填写。")
        saved = store.get_issue(item["issue_no"], item["source_file"], item["sheet_name"])
        if saved.analyses:
            latest = saved.analyses[0]
            if latest.stale:
                notes.append("已有整理基于旧版记录，待重新整理；旧结论未纳入报告。")
            else:
                analysis = latest.result.analysis
                notes.extend(
                    f"模型整理待补充（未经人工审核）：{text}"
                    for text in analysis.missing_information
                )
                notes.extend(
                    f"模型整理待核对（未经人工审核）：{warning.text}"
                    for warning in analysis.warnings
                )
        if notes:
            pending.append({**item, "notes": list(dict.fromkeys(notes))})
    now = datetime.now(timezone(timedelta(hours=8)))
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"issues-{now:%Y%m%d-%H%M%S-%f}.md"
    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "filters": filters.model_dump(),
        "sources": sorted({(item["source_file"], item["sheet_name"]) for item in items}),
        "total": len(items),
        "closed_count": closed_count,
        "open_count": len(opened),
        "uncertain_count": uncertain_count,
        "distributions": distributions,
        "open_issues": opened,
        "pending": pending,
        "file_path": str(target.resolve()),
    }
    environment = Environment(
        loader=PackageLoader("feature.issue_report", "templates"),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["display"] = display
    text = environment.get_template("report.md.j2").render(report=result, labels=FIELD_LABELS)
    target.write_text(text, encoding="utf-8")
    return text, result
