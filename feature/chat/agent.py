"""模型初始化及 Agent 组装；导入本模块不会发起网络请求。"""

from langchain.agents import create_agent
from langchain_chroma import Chroma
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from config import Settings
from feature.issue_store import IssueStore
from tools import build_tools

from .middleware import finish_issue_card
from .prompts import SYSTEM_PROMPT


def create_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=60,
        max_retries=0,
    )


def build_agent(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver,
    store: IssueStore | None = None,
    vectors: Chroma | None = None,
) -> CompiledStateGraph:
    return create_agent(
        model=model,
        tools=build_tools(model, store, vectors),
        middleware=[finish_issue_card],
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
