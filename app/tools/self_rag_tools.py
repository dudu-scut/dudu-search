"""
自建 RAG 知识库工具模块

提供与 RAGFlow 工具模块相同接口的 LangChain 工具：
list_knowledge_bases 用于发现可用知识库及其文档，
query_knowledge_base 用于向指定知识库发起知识问答。

所有工具都会从 ContextVar 获取当前用户的 group_id，确保跨组数据隔离。
"""

import asyncio

from app.api.context import get_current_group_id
from app.api.monitor import monitor
from app.metrics import TOOL_CALL_DURATION, TOOL_CALL_TOTAL
from app.self_rag.engine import get_rag_engine
from langchain_core.tools import tool


@tool
def list_knowledge_bases() -> str:
    """
    查询自建 RAG 中有哪些可用的知识库，以及每个知识库包含的文档信息

    作用：让模型先了解"哪个知识库能回答哪类内部文档问题"，再决定后续要向哪个知识库提问。
    调用 query_knowledge_base 之前，应先调用本工具确认知识库名称。
    :return: 有知识库时返回名称、描述、包含文档；无知识库或异常时返回中文提示
    """
    monitor.report_tool(tool_name="自建RAG知识库列表查询：list_knowledge_bases")

    try:
        group_id = get_current_group_id()
        engine = get_rag_engine()
        kb_list = engine.list_kbs(group_id=group_id)

        if not kb_list:
            return "当前没有任何可用的知识库。请先通过管理端上传文档创建知识库。"

        count_info = ""
        for kb in kb_list:
            name = kb.get("name", "未命名")
            desc = kb.get("description", "无描述")
            count_info += (
                f"知识库名称:{name};功能介绍：{desc}; "
                f"知识库ID：{kb.get('kb_id', '')}\n"
            )
        return count_info
    except Exception as e:
        return f"查询知识库列表异常，无可用知识库,异常信息:{str(e)}"


@tool
async def query_knowledge_base(kb_name: str, question: str) -> str:
    """
    向某个自建 RAG 知识库发起一次知识问答

    注意：调用此工具之前，必须先调用 list_knowledge_bases，确认可用知识库名称和描述。
    :param kb_name: 知识库名称，必须来自 list_knowledge_bases 返回结果
    :param question: 本次提问的问题
    :return: 基于知识库文档生成的回答文本；异常时返回中文错误提示
    """
    monitor.report_tool(
        tool_name="自建RAG知识库问答：query_knowledge_base",
        args={"kb_name": kb_name, "question": question},
    )

    TOOL_CALL_TOTAL.labels(tool_name="query_knowledge_base").inc()
    try:
        with TOOL_CALL_DURATION.labels(tool_name="query_knowledge_base").time():
            engine = get_rag_engine()

            # 校验当前用户组是否有权访问该知识库
            group_id = get_current_group_id()
            if group_id is None:
                # 防御性兜底：未分配组的用户默认归入组 1
                group_id = 1
            if not engine.check_kb_access(kb_name, group_id):
                return f"知识库 '{kb_name}' 不属于当前用户组，无权访问。"

            result = await asyncio.to_thread(engine.query, kb_name=kb_name, question=question)
            return result
    except Exception as e:
        return f"知识库提问失败，错误原因：{str(e)}"
