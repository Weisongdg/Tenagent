"""把现成向量库的写入、检索包装为两个 Agent 工具。"""

import json
from typing import Annotated

from langchain_chroma import Chroma
from langchain_core.tools import ToolException, tool
from pydantic import Field

from feature.issue_store import IssueStore

from .service import find_similar, save_issues


def create_vector_tools(store: IssueStore, vectors: Chroma):
    @tool
    def sync_issue_vectors() -> dict:
        """将当前问题库原始记录写入 Chroma；相同 ID 覆盖，不重复。更新 Excel 入库后再同步。"""
        try:
            return {"indexed_issues": save_issues(store, vectors)}
        except Exception as error:
            raise ToolException(
                f"向量写入失败（{type(error).__name__}），请检查问题库或嵌入服务。"
            ) from error

    @tool(return_direct=True, response_format="content_and_artifact")
    def search_similar_issues(
        query: Annotated[str, Field(min_length=1, max_length=8000)],
        k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5,
        area: str | None = None,
    ) -> tuple[str, dict]:
        """按含义查找相似问题，可限定区域如 L136。返回 No、原始行号和原文；候选数不是问题总数。"""
        try:
            documents = find_similar(vectors, query, k, area)
            parts = [f"返回 {len(documents)} 条相似候选（最多 {k} 条），不代表全部相关问题数量。"]
            for document in documents:
                raw, meta = json.loads(document.page_content), document.metadata
                parts.append(
                    f"**No.{meta['issue_no']}｜{raw['product']} · {raw['area']}"
                    f" · {raw['station']}**\n\n"
                    f"问题：{raw['problem_description']}\n\n"
                    f"原因原文：{raw['root_cause'] or '未填写'}\n\n"
                    f"措施原文：{raw['action'] or '未填写'}\n\n"
                    f"来源：{meta['source_file']} › {meta['sheet_name']}"
                    f" › 第 {meta['source_row']} 行。"
                )
            if not documents:
                parts.append("请先确认已经同步索引；未返回候选不代表不存在相关问题。")
            return "\n\n".join(parts), {
                "query": query,
                "candidates": [document.model_dump() for document in documents],
            }
        except Exception as error:
            raise ToolException(
                f"向量检索失败（{type(error).__name__}），请检查嵌入服务或索引。"
            ) from error

    tools = [sync_issue_vectors, search_similar_issues]
    for item in tools:
        item.handle_tool_error = True
    return tools
