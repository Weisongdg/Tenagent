"""使用官方中间件钩子，结束已产生固定卡片或查询结果的本轮请求。"""

from langchain.agents import AgentState
from langchain.agents.middleware import before_model
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime

DIRECT_RESULT_TOOLS = frozenset(
    {
        "analyze_excel_issue",
        "get_stored_issue",
        "search_issues",
        "summarize_issues",
        "search_similar_issues",
        "generate_issue_report",
    }
)


@before_model(can_jump_to=["end"])
def finish_issue_card(state: AgentState, runtime: Runtime) -> dict | None:
    # 当前版本仅在所有工具均 return_direct 时自动结束。
    # 混合调用时，在下一次模型调用前检查本轮是否已有固定结果或工具错误。
    for message in reversed(state["messages"]):
        if message.type == "human":
            break
        if isinstance(message, ToolMessage) and message.name in DIRECT_RESULT_TOOLS:
            return {"jump_to": "end"}
    return None
