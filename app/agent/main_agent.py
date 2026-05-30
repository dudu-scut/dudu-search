"""
主智能体组装与异步执行模块

负责把模型、主提示词、文件类工具和三个专家子智能体组装成 DeepAgent，
并提供 run_deep_agent 作为后续 API 层调用的统一入口。运行时还会为每个
session_id 创建独立工作目录，并把工具调用、子智能体调用和最终结果推送给前端。
"""

import asyncio
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from deepagents import create_deep_agent

from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.llm import model
from app.config import settings
from app.agent.prompts import main_agent_content
from app.agent.subagents.database_query_agent import database_query_agent
from app.agent.subagents.knowledge_base_agent import knowledge_base_agent
from app.agent.subagents.network_search_agent import network_search_agent
from app.api.context import (
    reset_session_context,
    set_current_group_id,
    set_session_context,
    set_thread_context,
)
from app.api.monitor import monitor
from app.logging_config import get_logger
from app.storage.memory_service import get_memory_service
from app.exceptions import LLMError, LLMTimeoutError

logger = get_logger("main_agent")

# 文件类工具由主智能体直接掌握，负责读取上传附件和生成最终交付文档
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.upload_file_read_tool import read_file_content

# 基础工具和子智能体列表在所有 agent 实例间共享
_BASE_TOOLS = [generate_markdown, convert_md_to_pdf, read_file_content]
_BASE_SUBAGENTS = [database_query_agent, network_search_agent, knowledge_base_agent]


# ── LLM 重试逻辑 ──


def _is_retryable_error(exception: Exception) -> bool:
    """判断异常是否可重试（仅网络类异常可重试）。"""
    if isinstance(exception, (httpx.ReadTimeout, httpx.ConnectError,
                               httpx.RemoteProtocolError, ConnectionError,
                               TimeoutError)):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        if exception.response.status_code in (429, 503, 502):
            return True
        return False
    return False


async def _retryable_llm_invoke(model, messages):
    """带重试的 LLM 调用。"""
    last_exception = None
    for attempt in range(3):
        try:
            return await model.ainvoke(messages)
        except Exception as e:
            last_exception = e
            if not _is_retryable_error(e) or attempt == 2:
                break
            wait_time = 2 ** attempt
            logger.warning("LLM 调用失败，即将重试", wait_time=wait_time, attempt=f"{attempt+1}/3", exc_info=True)
            await asyncio.sleep(wait_time)

    if isinstance(last_exception, (httpx.ReadTimeout, TimeoutError)):
        raise LLMTimeoutError(f"LLM 调用超时，已重试 3 次: {last_exception}")
    raise LLMError(f"LLM 调用失败: {last_exception}")


async def _retryable_astream(agent, input_data, config):
    """带重试的 Agent 流式执行。"""
    last_exception = None
    for attempt in range(3):
        try:
            async for chunk in agent.astream(input_data, config=config):
                yield chunk
            return
        except Exception as e:
            last_exception = e
            if not _is_retryable_error(e) or attempt == 2:
                break
            wait_time = 2 ** attempt
            logger.warning("Agent 流式执行失败，即将重试", wait_time=wait_time, attempt=f"{attempt+1}/3", exc_info=True)
            await asyncio.sleep(wait_time)

    if isinstance(last_exception, (httpx.ReadTimeout, TimeoutError)):
        raise LLMTimeoutError(f"Agent 流式执行超时，已重试 3 次: {last_exception}")
    raise LLMError(f"Agent 流式执行失败: {last_exception}")


def _build_main_agent(current_date: str):
    """按当前日期构建主智能体，确保系统提示词包含正确的时间信息。

    DeepAgents 子智能体在独立会话中运行，无法读取父会话用户消息，
    因此日期必须写进系统提示词，子智能体才能感知。

    使用 PostgresSaver 替代 InMemorySaver，实现检查点持久化。
    """
    postgres_uri = settings.POSTGRES_SYNC_URI
    checkpointer = PostgresSaver.from_conn_string(postgres_uri)
    return create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"].format(
            current_date=current_date
        ),
        tools=_BASE_TOOLS,
        checkpointer=checkpointer,
        subagents=_BASE_SUBAGENTS,
    )

# 当前文件位于 app/agent/main_agent.py，parents[1] 即 app 目录
project_root_path = Path(__file__).parents[1].resolve()


async def _persist_message(thread_id: str, role: str, content: str) -> None:
    """异步持久化单条消息到 PostgreSQL。"""
    try:
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO messages (thread_id, role, content) VALUES ($1, $2, $3)",
                thread_id, role, content,
            )
    except Exception as e:
        logger.warning("消息持久化失败", exc_info=True)


async def _persist_event(
    thread_id: str, event_type: str, message: str, payload: dict | None = None
) -> None:
    """异步持久化 Agent 事件到 PostgreSQL。"""
    try:
        import json
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent_events (thread_id, event_type, message, payload) "
                "VALUES ($1, $2, $3, $4)",
                thread_id, event_type, message,
                json.dumps(payload or {}, ensure_ascii=False),
            )
    except Exception as e:
        logger.warning("事件持久化失败", exc_info=True)


async def _ensure_session(thread_id: str, group_id: int | None = None) -> None:
    """确保 sessions 表中存在对应记录。"""
    try:
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sessions (thread_id, status, group_id) VALUES ($1, 'running', $2) "
                "ON CONFLICT (thread_id) DO UPDATE SET status = 'running', started_at = NOW()",
                thread_id, group_id,
            )
    except Exception as e:
        logger.warning("会话记录失败", exc_info=True)


async def _complete_session(thread_id: str) -> None:
    """标记会话为已完成。"""
    try:
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET status = 'completed', completed_at = NOW() "
                "WHERE thread_id = $1",
                thread_id,
            )
    except Exception as e:
        logger.warning("会话完成标记失败", exc_info=True)


async def _run_memory_consolidation(thread_id: str) -> None:
    """后台执行记忆巩固 Pipeline。"""
    try:
        memory_service = get_memory_service()
        result = await memory_service.consolidate_session(thread_id)
        logger.info(
            "会话巩固完成",
            thread_id=thread_id,
            title=result.get('title'),
            facts_count=len(result.get('facts', [])),
        )
    except Exception as e:
        logger.warning("会话巩固异常", exc_info=True)


async def run_deep_agent(task_query, session_id, group_id=None):
    """
    异步流式执行主智能体

    API 层会为每次任务传入用户问题和 session_id。本函数负责准备会话目录、
    复制上传文件、写入 ContextVar，并在流式执行过程中把关键事件上报给前端。
    :param task_query: 前端提交的原始任务问题
    :param session_id: 当前任务 ID，同时用于 thread_id、输出目录和 WebSocket 定向推送
    :param group_id: 用户组 ID，用于知识库等工具层隔离过滤（可选）
    """
    logger.info("开始执行会话", session_id=session_id)

    # 设置 group_id 到 ContextVar，供知识库工具等深层调用读取
    if group_id is not None:
        set_current_group_id(group_id)

    # 持久化会话记录
    asyncio.create_task(_ensure_session(session_id, group_id))

    # 每个会话独立使用 output/session_{session_id}，避免不同用户的产物互相覆盖
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 前端和工具使用绝对路径；提示词里只给模型相对路径，降低模型误用系统绝对路径的概率
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace(
        "\\", "/"
    )

    # 上传文件先落在 updated/session_{session_id}，执行前复制到本次 output 工作目录
    # 这样读文件工具和生成文件工具都只需要围绕同一个 session_dir 工作
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = ""
    if updated_dir_path.exists():
        files = [f.name for f in updated_dir_path.iterdir() if f.is_file()]
        if files:
            for filename in files:
                # copy2 会保留上传文件的修改时间、权限等元数据，便于后续排查文件来源
                shutil.copy2(updated_dir_path / filename, session_dir / filename)

            # 把上传文件列表注入用户消息，提醒模型先调用 read_file_content 获取附件内容
            updated_info_prompt = (
                "\n    [已上传文件] 已加载到工作目录:\n"
                + "\n".join([f"    - {f}" for f in files])
                + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
            )

    # ContextVar 让深层工具无需显式传参，也能拿到当前会话目录和 WebSocket thread_id
    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)

    # 前端拿到工作目录后，可以展示本次任务生成的 Markdown/PDF 等产物
    monitor.report_session_dir(session_dir_str)

    # checkpointer 依赖 thread_id 区分会话记忆；同一 session_id 会复用同一条执行上下文
    config = {"configurable": {"thread_id": session_id}}

    # 当前时间注入，解决 LLM 不知道今天日期的问题（东八区）
    now = datetime.now(timezone(timedelta(hours=8)))
    time_context = now.strftime("%Y年%m月%d日 %H:%M (UTC+8)")

    # 按当前日期构建 agent，确保主智能体和子智能体的系统提示词都包含正确时间
    agent = _build_main_agent(time_context)

    # 工作环境指令是运行时动态补充的，约束模型只在当前会话目录读写文件
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    # 检索并注入相关记忆上下文
    memory_context = ""
    try:
        memory_service = get_memory_service()
        memory_context = await memory_service.build_context(session_id, task_query)
    except Exception as e:
        logger.warning("记忆上下文获取失败", exc_info=True)

    full_query = task_query + path_instruction
    if memory_context:
        full_query = (
            f"【记忆上下文 — 请参考以下信息辅助本次任务】\n"
            f"{memory_context}\n\n"
            f"【用户问题】\n{full_query}"
        )

    try:
        # astream 会持续产出模型节点、工具节点和子智能体节点的状态片段
        # 使用 _retryable_astream 包装，网络异常时自动重试
        async for chunk in _retryable_astream(
            agent,
            {"messages": [{"role": "user", "content": full_query}]},
            config=config,
        ):
            # chunk 形如 {"model": {"messages": [...]}}，这里主要关心模型最新消息
            for node_name, state in chunk.items():
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if messages and isinstance(messages, list):
                    last_msg = messages[-1]
                    if node_name == "model":
                        if last_msg.tool_calls:
                            # DeepAgents 调用子智能体时，本质上会产生名为 task 的工具调用
                            for tool_call in last_msg.tool_calls:
                                if tool_call["name"] == "task":
                                    # 子智能体调用单独上报，前端可以展示“正在调用哪个专家助手”
                                    monitor.report_assistant(
                                        tool_call["args"]["subagent_type"],
                                        {
                                            "description": tool_call["args"][
                                                "description"
                                            ]
                                        },
                                    )
                        elif last_msg.content:
                            # 模型没有继续调用工具时，最新文本内容就是本轮可反馈给前端的结果
                            logger.info("主智能体执行结果", result_preview=last_msg.content[:100])
                            monitor.report_task_result(last_msg.content)
                            # 持久化 assistant 消息
                            asyncio.create_task(_persist_message(
                                session_id, "assistant", last_msg.content
                            ))

    except asyncio.CancelledError:
        monitor.report_task_cancelled()
        raise
    except Exception as e:
        # 异步执行异常也走 monitor，保证前端能收到明确错误事件
        monitor._emit("error", f"执行主智能发生异常信息：{str(e)}")
    finally:
        # 标记会话完成
        asyncio.create_task(_complete_session(session_id))
        # 异步触发记忆巩固（不阻塞会话完成）
        try:
            asyncio.create_task(_run_memory_consolidation(session_id))
        except Exception:
            pass
        # 任务结束后恢复 ContextVar，避免后续请求复用到本次会话目录或 thread_id
        reset_session_context(session_dir_token, session_id_token)


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        run_deep_agent("从网络查询机器人信息，并生成Markdown文件", "test_session_001")
    )
