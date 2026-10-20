"""问题库的 LangChain 工具，使用启动时注入的存储服务。"""

from typing import Annotated

from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import Field

from feature.excel_reader import ExcelReadError
from feature.excel_reader.reader import DEFAULT_FILE, DEFAULT_SHEET

from .service import IssueStore, IssueStoreError


def create_store_tools(store: IssueStore) -> list[BaseTool]:
    @tool
    def import_excel_issues(file_path: str = DEFAULT_FILE, sheet_name: str = DEFAULT_SHEET) -> dict:
        """将 Excel 整张工作表的原始问题导入本地问题库，不调用模型整理。

        默认使用项目的 Process_Issue_Tracking.xlsx / ProcessIssueTracking。
        返回新增、更新、跳过数量。重复或缺失 No 将拒绝整批导入。
        """
        try:
            return store.import_excel(file_path, sheet_name).model_dump(mode="json")
        except (ExcelReadError, IssueStoreError) as error:
            raise ToolException(str(error)) from error

    @tool
    def list_stored_issues(
        product: str | None = None,
        status: str | None = None,
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20,
    ) -> dict:
        """分页列出本地问题库，包含来源、编号和原始状态；不读取 Excel 或生成卡片。

        product、status 按原始字段精确筛选，例如 Summit、under tracking、Closed。
        返回总数和当前页，下一页使用 offset + limit。
        """
        try:
            return store.list_issues(product, status, offset, limit).model_dump(mode="json")
        except IssueStoreError as error:
            raise ToolException(str(error)) from error

    @tool(return_direct=True, response_format="content_and_artifact")
    def get_stored_issue(
        issue_no: Annotated[int, Field(strict=True, ge=1)],
        file_path: str = DEFAULT_FILE,
        sheet_name: str = DEFAULT_SHEET,
        history_limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 1,
    ) -> tuple[str, dict]:
        """查看本地已存问题及卡片，不调用整理模型，也不重新读取 Excel。

        用 No、来源文件和工作表定位。history_limit 控制返回的整理历史数量，最新在前。
        无卡片时返回原始记录；过期卡片明确标注过期。模型整理结果均尚未经人工审核。
        """
        try:
            saved = store.get_issue(issue_no, file_path, sheet_name, history_limit)
            if saved.analyses:
                parts = []
                for analysis in saved.analyses:
                    label = (
                        "已过期，来源记录或单元格位置已变更"
                        if analysis.stale
                        else "与当前入库记录一致"
                    )
                    parts.append(
                        f"保存记录 #{analysis.analysis_id}｜{analysis.created_at}｜{label}"
                        f"｜模型整理，未经人工审核\n\n{analysis.card}"
                    )
                content = "\n\n---\n\n".join(parts)
            else:
                content = (
                    f"No.{saved.issue_no} 已入库，尚无整理卡片。\n\n"
                    f"来源：{saved.source_file} › {saved.sheet_name}"
                    f" › 第 {saved.issue.source_row} 行\n\n"
                    "以下为原始记录：\n\n"
                    f"```json\n{saved.issue.model_dump_json(indent=2)}\n```"
                )
            return content, saved.model_dump(mode="json")
        except IssueStoreError as error:
            raise ToolException(str(error)) from error

    tools = [import_excel_issues, list_stored_issues, get_stored_issue]
    for item in tools:
        item.handle_tool_error = True
    return tools
