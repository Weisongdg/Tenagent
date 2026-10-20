"""终端交互：命令在本地执行，普通消息交给 ChatService。"""

import sqlite3
from collections.abc import Callable

from .service import ChatError, ChatService
from .sessions import Session, SessionStore

HELP = """直接输入文字即可聊天。
/new [标题]  新建并切换会话
/list        列出会话（序号按创建顺序排列）
/use 序号    切换会话
/history     查看当前会话历史
/help        查看帮助
/exit        退出
"""


def _show_sessions(
    sessions: list[Session], current: Session | None, output: Callable[[str], None]
) -> None:
    for number, session in enumerate(sessions, start=1):
        marker = "*" if current and session.thread_id == current.thread_id else " "
        output(f"{marker} {number}. {session.title}  [{session.thread_id}]")


def _session_at(sessions: list[Session], value: str) -> Session:
    try:
        number = int(value)
    except ValueError:
        raise ValueError("请输入 /list 中的有效会话序号。") from None
    if not 1 <= number <= len(sessions):
        raise ValueError("会话序号超出范围，请用 /list 查看。")
    return sessions[number - 1]


def run_chat(
    service: ChatService,
    store: SessionStore,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    output("Tenagent 基础聊天 · 输入 /help 查看命令")
    try:
        sessions = store.list_sessions()
        if not sessions:
            current = store.create("默认会话")
        else:
            _show_sessions(sessions, None, output)
            while True:
                choice = input_fn("选择序号（回车选最新创建会话，或 /new 标题、/exit）：").strip()
                if choice == "/exit":
                    return
                if choice == "/new" or choice.startswith("/new "):
                    current = store.create(choice[4:].strip())
                    break
                try:
                    current = _session_at(sessions, choice or str(len(sessions)))
                    break
                except ValueError as error:
                    output(str(error))

        output(f"当前会话：{current.title}")
        while True:
            text = input_fn(f"\n[{current.title}] 你：").strip()
            if not text:
                continue
            if not text.startswith("/"):
                try:
                    output(f"助手：{service.reply(current.thread_id, text)}")
                except ChatError as error:
                    output(str(error))
                continue

            parts = text.split(maxsplit=1)
            command, argument = parts[0], parts[1] if len(parts) == 2 else ""
            try:
                if command == "/exit":
                    return
                elif command == "/help":
                    output(HELP)
                elif command == "/new":
                    current = store.create(argument)
                    output(f"已切换到新会话：{current.title}")
                elif command == "/list":
                    _show_sessions(store.list_sessions(), current, output)
                elif command == "/use":
                    current = _session_at(store.list_sessions(), argument)
                    output(f"已切换到会话：{current.title}")
                elif command == "/history":
                    messages = service.history(current.thread_id)
                    if not messages:
                        output("当前会话还没有消息。")
                    for message in messages:
                        role = {"human": "你", "ai": "助手", "tool": "工具"}.get(
                            message.type, message.type
                        )
                        output(f"{role}：{message.text}")
                else:
                    output("未知命令，请输入 /help 查看帮助。")
            except ValueError as error:
                output(str(error))
            except sqlite3.Error:
                output("会话数据读取或保存失败，请检查数据库路径和文件权限。")
    except (EOFError, KeyboardInterrupt):
        output("\n已退出，已保存的会话可在下次启动时继续。")
