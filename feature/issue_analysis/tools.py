"""通过工具工厂注入现有模型，复用读取器并按 No 选择唯一记录。"""

from typing import Annotated

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import Field

from feature.excel_reader import ExcelReadError, read_issues
from feature.excel_reader.reader import DEFAULT_FILE, DEFAULT_SHEET
from feature.issue_store import IssueStore, IssueStoreError

from .analyzer import IssueAnalysisError, analyze_issue
from .evidence import locate_evidence
from .renderer import render_issue_card
from .schemas import IssueAnalysisResult


def create_analysis_tools(model: BaseChatModel, store: IssueStore | None = None) -> list[BaseTool]:
    @tool(return_direct=True, response_format="content_and_artifact")
    def analyze_excel_issue(
        issue_no: Annotated[int, Field(strict=True, ge=1)],
        file_path: str = DEFAULT_FILE,
        sheet_name: str = DEFAULT_SHEET,
    ) -> tuple[str, dict]:
        """整理 Excel 中 No 等于 issue_no 的唯一问题，不是 Excel 行号。

        直接返回正式整理卡，涵盖中文摘要、原因、措施、现场观察、有效性验证和原文依据。
        默认读取项目中的 Process_Issue_Tracking.xlsx / ProcessIssueTracking。
        仅整理一条，保留原始记录；不推测未知信息，不提出新的措施，不写回文件。
        正式卡片由固定模板生成，不要在外层改写。失败时报告失败，不生成成功卡片。
        启用本地问题库时，成功结果自动保存原始记录、完整整理结果和固定卡片。
        """
        try:
            result = read_issues(file_path, sheet_name)
            matches = [
                issue
                for issue in result.issues
                if not isinstance(issue.no, bool) and issue.no in (issue_no, str(issue_no))
            ]
            if not matches:
                raise IssueAnalysisError(f"找不到 No={issue_no} 的问题记录。")
            if len(matches) > 1:
                raise IssueAnalysisError(f"No={issue_no} 存在重复记录，请先确认编号。")
            issue = matches[0]
            expected_fingerprint = (
                store.current_fingerprint(issue_no, result.source_file, result.sheet_name)
                if store is not None
                else None
            )
            analysis = analyze_issue(issue, model)
            output = IssueAnalysisResult(
                source_file=result.source_file,
                sheet_name=result.sheet_name,
                column_mapping=result.column_mapping,
                issue=issue,
                analysis=analysis,
                evidence_locations=locate_evidence(issue, analysis, result.column_mapping),
            )
            card = render_issue_card(output)
            if store is not None:
                store.save_analysis(output, card, expected_fingerprint)
            return card, output.model_dump(mode="json")
        except (ExcelReadError, IssueAnalysisError, IssueStoreError) as error:
            raise ToolException(str(error)) from error

    analyze_excel_issue.handle_tool_error = True
    return [analyze_excel_issue]
