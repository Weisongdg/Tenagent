"""直接使用 LangChain Chroma 写入和检索，不自建索引管理。"""

import json

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from sqlmodel import select

from config import PROJECT_DIR, Settings
from feature.issue_store import IssueStore
from feature.issue_store.models import IssueRecord


def create_vector_store(settings: Settings):
    client = chromadb.PersistentClient(path=str(PROJECT_DIR / "data/vectors"))
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        check_embedding_ctx_length=False,
        chunk_size=8,
        timeout=60,
        max_retries=0,
    )
    return Chroma(
        client=client, collection_name="process_issues", embedding_function=embeddings
    ), client


def save_issues(store: IssueStore, vectors: Chroma) -> int:
    with store.repository.transaction() as session:
        records = session.exec(select(IssueRecord).order_by(IssueRecord.id)).all()
        documents = [
            Document(
                page_content=json.dumps(record.raw_snapshot["values"], ensure_ascii=False),
                metadata={
                    "issue_id": record.id,
                    "issue_no": record.issue_no,
                    "source_file": record.source_file,
                    "sheet_name": record.sheet_name,
                    "source_row": record.raw_snapshot["values"]["source_row"],
                    "area": str(record.raw_snapshot["values"]["area"] or ""),
                },
            )
            for record in records
        ]
        ids = [f"issue-{record.id}" for record in records]
    if not documents:
        raise ValueError("问题库为空，请先导入 Excel。")
    # Chroma 按相同 ID 覆盖写入；重复同步不会增加重复记录。
    vectors.add_documents(documents, ids=ids)
    return len(documents)


def find_similar(vectors: Chroma, query: str, k: int, area: str | None):
    if not query.strip():
        raise ValueError("查询不能为空。")
    # Qwen 官方建议的查询格式；不修改入库原文。
    query = f"Instruct: Retrieve relevant historical production issue records.\nQuery: {query}"
    return vectors.similarity_search(query, k=k, filter={"area": area} if area else None)
