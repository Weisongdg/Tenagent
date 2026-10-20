"""把普通读取函数包装成 LangChain 工具，不重复实现解析。"""

from langchain_core.tools import ToolException, tool

from .reader import DEFAULT_FILE, DEFAULT_SHEET, ExcelReadError, read_issues


@tool
def read_excel_issues(
    file_path: str = DEFAULT_FILE,
    sheet_name: str = DEFAULT_SHEET,
) -> dict:
    """读取本地问题追踪 .xlsx 的全部记录，返回原始字段、数量和来源行列。

    默认文件为项目根目录的 Process_Issue_Tracking.xlsx，默认工作表为
    ProcessIssueTracking。相对路径基于项目根目录。仅读取单元格，不提取内嵌图片。
    不清洗状态、不推断年份、不执行或计算公式。文件须采用约定的 12 列模板。
    """
    try:
        return read_issues(file_path, sheet_name).model_dump(mode="json")
    except ExcelReadError as error:
        raise ToolException(str(error)) from error


# 让框架把预期的读取失败作为工具错误返回给 Agent，而不是中断整轮聊天。
read_excel_issues.handle_tool_error = True

TOOLS = [read_excel_issues]
