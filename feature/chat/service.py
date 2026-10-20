"""每轮只提交新消息；上下文的恢复和保存交给 checkpointer。"""

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from .middleware import DIRECT_RESULT_TOOLS


class ChatError(Exception):
    """可展示的聊天失败信息，不包含上游响应或密钥。"""


class ChatService:
    def __init__(self, agent: CompiledStateGraph) -> None:
        self.agent = agent

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}}

    def reply(self, thread_id: str, text: str) -> str:
        if not text.strip():
            raise ValueError("消息不能为空。")
        try:
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": text}]},
                config=self._config(thread_id),
            )
            message = result["messages"][-1]
            # return_direct 的卡片、查询结果或错误已在工具中产生，不能再交给模型改写。
            # 只收集本轮结果；同轮混有读取工具时，不能仅检查最后一条工具消息。
            direct_results = []
            for item in reversed(result["messages"]):
                if item.type == "human":
                    break
                if isinstance(item, ToolMessage) and item.name in DIRECT_RESULT_TOOLS:
                    direct_results.append(item.text)
            if direct_results:
                return "\n\n".join(reversed(direct_results))
            if not isinstance(message, AIMessage):
                raise TypeError("Agent did not return an assistant message")
            return message.text
        except Exception as error:
            # 不拼入上游错误正文，避免服务响应泄露凭据或其他请求数据。
            raise ChatError(
                f"聊天调用失败（{type(error).__name__}）。本轮用户消息可能已保存，"
                "可用 /history 检查；未自动重试，请检查模型配置或网络后继续。"
            ) from error

    def history(self, thread_id: str) -> list[BaseMessage]:
        snapshot = self.agent.get_state(self._config(thread_id))
        return list(snapshot.values.get("messages", []))
