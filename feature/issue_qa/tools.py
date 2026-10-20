"""查询和统计工具只接收筛选参数，数量、原文和来源全部由程序生成。"""

from typing import Annotated

from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import Field

from feature.issue_store import IssueStore

from .renderer import render_search, render_statistics
from .schemas import GroupField, QueryFilters
from .service import IssueQueryError, IssueQueryService


def create_qa_tools(store: IssueStore) -> list[BaseTool]:
    service = IssueQueryService(store)

    @tool(return_direct=True, response_format="content_and_artifact")
    def search_issues(
        filters: QueryFilters,
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 10,
    ) -> tuple[str, dict]:
        """查询本地原始问题，回答“L136 有哪些问题”“哪些涉及螺丝安装”等。

        filters 的 product/area/station/status 精确匹配原值，省略表示不限。
        中文、英文检索词可展开为 keywords，如 ["螺丝", "螺钉", "screw"]，keyword_mode=any。
        多个词必须均出现时用 all，可分布在问题描述、原因、措施中；只做字面匹配，不确认语义分类。
        返回数据库计算的匹配总数、当前页、No、原始行号和命中原文。下一页保留条件并使用 next_offset。
        filters={} 查询全部入库问题。file_path/sheet_name 可限定来源，省略会查全部来源。
        直接展示固定回答，不重新计数或改写，不调用整理器。
        """
        try:
            result = service.search(filters, offset, limit)
            return render_search(result), result.model_dump(mode="json")
        except IssueQueryError as error:
            raise ToolException(str(error)) from error

    @tool(return_direct=True, response_format="content_and_artifact")
    def summarize_issues(
        filters: QueryFilters,
        group_by: GroupField,
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 10,
    ) -> tuple[str, dict]:
        """统计本地原始问题，按 product/area/station/status 分组，数量由数据库计算。

        如“L136 的问题状态分布”：filters={"area":"L136"}, group_by="status"。
        如“各区域多少问题”：filters={}, group_by="area"。筛选规则与 search_issues 一致。
        offset/limit 对分组分页，总数始终是全部匹配问题数；每组附最多 2 条带 No 和行号的示例。
        按原始状态分组，不合并不同状态文字，不推断措施有效性；直接展示固定统计结果。
        """
        try:
            result = service.summarize(filters, group_by, offset, limit)
            return render_statistics(result), result.model_dump(mode="json")
        except IssueQueryError as error:
            raise ToolException(str(error)) from error

    tools = [search_issues, summarize_issues]
    for item in tools:
        item.handle_tool_error = True
    return tools
