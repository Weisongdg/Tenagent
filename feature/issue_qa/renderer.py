"""Jinja2 固定输出数量、分页和来源，外层模型不改写查询结论。"""

import json

from jinja2 import Environment, PackageLoader, StrictUndefined

from .schemas import QueryFilters, SearchResult, StatisticsResult

FIELD_LABELS = {
    "file_path": "来源文件",
    "sheet_name": "工作表",
    "product": "产品",
    "area": "区域",
    "station": "工位",
    "status": "原始状态",
    "problem_description": "问题描述",
    "root_cause": "原因字段",
    "action": "措施字段",
}


def display(value) -> str:
    if value is None:
        return "未填写"
    if value == "":
        return "空字符串"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _conditions(filters: QueryFilters) -> str:
    parts = [
        f"{FIELD_LABELS[name]}={value}"
        for name, value in filters.model_dump().items()
        if name in FIELD_LABELS and value is not None
    ]
    return "；".join(parts) or "全部已入库来源，无字段筛选"


_ENVIRONMENT = Environment(
    loader=PackageLoader("feature.issue_qa", "templates"),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_ENVIRONMENT.filters["display"] = display


def render_search(result: SearchResult) -> str:
    return (
        _ENVIRONMENT.get_template("search.md.j2")
        .render(
            result=result.model_dump(mode="json"),
            conditions=_conditions(result.filters),
            labels=FIELD_LABELS,
        )
        .strip()
    )


def render_statistics(result: StatisticsResult) -> str:
    return (
        _ENVIRONMENT.get_template("statistics.md.j2")
        .render(
            result=result.model_dump(mode="json"),
            conditions=_conditions(result.filters),
            labels=FIELD_LABELS,
        )
        .strip()
    )
