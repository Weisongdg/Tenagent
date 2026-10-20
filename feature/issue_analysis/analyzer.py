"""使用 LangChain 的结构化输出完成一次整理，不自建解析和重试循环。"""

from langchain_core.language_models import BaseChatModel

from feature.excel_reader import RawIssue

from .evidence import validate_evidence
from .prompts import ANALYSIS_PROMPT
from .schemas import IssueAnalysis


class IssueAnalysisError(ValueError):
    """模型调用、结构化输出或原文引用未通过校验。"""


def analyze_issue(issue: RawIssue, model: BaseChatModel) -> IssueAnalysis:
    """仅发送这一条记录，不读取配置、文件或其他会话历史。"""
    try:
        chain = ANALYSIS_PROMPT | model.with_structured_output(
            IssueAnalysis, method="function_calling"
        )
        result = chain.invoke({"issue_json": issue.model_dump_json()})
        analysis = IssueAnalysis.model_validate(result)
    except Exception as error:
        # 不把可能包含凭据、原始响应或请求正文的上游异常直接展示给用户。
        raise IssueAnalysisError(
            f"单条问题整理失败（{type(error).__name__}）。"
            "未生成有效整理结果，请检查模型服务是否支持工具调用和结构化输出；未自动重试。"
        ) from error

    try:
        validate_evidence(issue, analysis)
    except ValueError as error:
        raise IssueAnalysisError(f"原文引用校验失败：{error}未生成有效整理结果。") from error
    return analysis
