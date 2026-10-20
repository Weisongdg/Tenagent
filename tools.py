"""统一工具入口：启动时注入已有模型，汇总各功能工具。"""

from langchain_chroma import Chroma
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from feature.excel_reader.tools import TOOLS as EXCEL_TOOLS
from feature.issue_analysis.tools import create_analysis_tools
from feature.issue_qa.tools import create_qa_tools
from feature.issue_report.tools import create_report_tools
from feature.issue_store import IssueStore
from feature.issue_store.tools import create_store_tools
from feature.issue_vectors.tools import create_vector_tools


def build_tools(
    model: BaseChatModel, store: IssueStore | None = None, vectors: Chroma | None = None
) -> list[BaseTool]:
    return [
        *EXCEL_TOOLS,
        *create_analysis_tools(model, store),
        *(create_store_tools(store) if store is not None else []),
        *(create_qa_tools(store) if store is not None else []),
        *(create_report_tools(store) if store is not None else []),
        *(create_vector_tools(store, vectors) if store is not None and vectors is not None else []),
    ]
