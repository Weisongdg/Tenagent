"""现有 Agent 通过一个工具生成报告；不调用额外模型。"""

from langchain_core.tools import ToolException, tool

from feature.issue_qa.schemas import QueryFilters
from feature.issue_store import IssueStore

from .service import generate_report


def create_report_tools(store: IssueStore):
    @tool(return_direct=True, response_format="content_and_artifact")
    def generate_issue_report(filters: QueryFilters) -> tuple[str, dict]:
        """生成并保存整体或指定范围的问题报告，filters={} 表示全部已入库记录。

        支持现有产品、区域、工位、原始状态、来源和字面关键词筛选，如 filters={"area":"L136"}。
        包含报告范围，按产品/区域/工位/原始状态分布，明确未关闭的问题，以及待确认事项。
        数据由程序完整查询和计算，保留原文、No、行号；不支持时间范围或语义 Top k 报告。
        不生成措施汇总、分类或风险评级，不批量调用整理模型。直接展示固定报告和保存路径。
        """
        try:
            return generate_report(store, filters)
        except Exception as error:
            raise ToolException(
                f"报告生成失败（{type(error).__name__}），未返回有效报告；请检查数据和输出目录。"
            ) from error

    generate_issue_report.handle_tool_error = True
    return [generate_issue_report]
