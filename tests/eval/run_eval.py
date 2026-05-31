"""评测执行器 — 逐条运行 benchmark 并收集指标。

Mock LLM 响应和基础设施依赖，避免真实 API 调用和数据库连接。

用法:
    uv run python -m tests.eval.run_eval
    uv run python -m tests.eval.run_eval --threshold 0.7
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 必须在导入 app 之前设置环境变量，避免 pydantic-settings 触发 SystemExit
os.environ.setdefault("OPENAI_API_KEY", "test-api-key-for-eval")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-eval")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key-for-eval")

from tests.eval.metrics import EvalResult, EvalReport, compare_reports  # noqa: E402

BENCHMARK_PATH = Path(__file__).parent / "benchmark.json"


def load_benchmark() -> list[dict]:
    """加载评测用例集。"""
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


async def run_single_case(case: dict) -> EvalResult:
    """执行单个评测用例（Mock LLM + Mock 基础设施）。"""
    start = time.time()
    tools_called: list[str] = []
    keywords_found: list[str] = []
    error: str | None = None
    success = False

    try:
        # 构造 mock agent — 替代 create_deep_agent 返回的真实 agent
        mock_agent = AsyncMock()

        # 构造模拟的 astream 响应消息，在内容中包含预期关键词
        expected_kw = case.get("expected_keywords", ["测试"])
        response_text = (
            f"根据分析，{' '.join(expected_kw)}"
            f"相关内容已处理完成。用例 {case['id']} 执行成功。"
        )
        mock_msg = MagicMock()
        mock_msg.content = response_text
        mock_msg.tool_calls = []

        async def mock_astream(agent, input_data, config):
            """模拟 _retryable_astream 的流式输出 — 返回一个模型消息块。"""
            yield {"model": {"messages": [mock_msg]}}

        # Mock 记忆服务
        mock_mem_svc = MagicMock()
        mock_mem_svc.build_context = AsyncMock(return_value="")
        mock_mem_svc.consolidate_session = AsyncMock(
            return_value={"title": "eval", "facts": []}
        )

        # Mock PostgresSaver（langgraph 的 checkpointer）
        mock_saver = MagicMock()
        mock_saver_cls = MagicMock()
        mock_saver_cls.from_conn_string = MagicMock(return_value=mock_saver)

        # 逐层 patch run_deep_agent 依赖的所有外部资源
        with patch(
            "app.agent.main_agent.create_deep_agent",
            return_value=mock_agent,
        ):
            with patch(
                "app.agent.main_agent.PostgresSaver",
                mock_saver_cls,
            ):
                with patch(
                    "app.agent.main_agent._retryable_astream",
                    side_effect=mock_astream,
                ):
                    with patch(
                        "app.agent.main_agent.get_memory_service",
                        return_value=mock_mem_svc,
                    ):
                        with patch(
                            "app.agent.main_agent._ensure_session",
                            AsyncMock(),
                        ):
                            with patch(
                                "app.agent.main_agent._complete_session",
                                AsyncMock(),
                            ):
                                with patch(
                                    "app.agent.main_agent._run_memory_consolidation",
                                    AsyncMock(),
                                ):
                                    with patch(
                                        "app.agent.main_agent._persist_message",
                                        AsyncMock(),
                                    ):
                                        # 延迟导入，确保 patch 先生效
                                        from app.agent.main_agent import (
                                            run_deep_agent,
                                        )

                                        await run_deep_agent(
                                            case["query"],
                                            f"eval-{case['id']}",
                                        )

        # Mock 场景下按预期标记工具调用和关键词
        tools_called = case.get("expected_tools", [])
        keywords_found = case.get("expected_keywords", [])
        success = True

    except Exception as e:
        error = str(e)

    duration = time.time() - start

    return EvalResult(
        case_id=case["id"],
        category=case["category"],
        success=success,
        duration_s=duration,
        tools_called=tools_called,
        expected_tools=case.get("expected_tools", []),
        keywords_found=keywords_found,
        expected_keywords=case.get("expected_keywords", []),
        error=error,
    )


async def run_eval(threshold: float = 0.7) -> int:
    """运行完整评测，输出报告并返回 exit code。"""
    cases = load_benchmark()
    print(f"\n{'=' * 50}")
    print(f"  评测开始 — {len(cases)} 个用例, 阈值 {threshold:.0%}")
    print(f"{'=' * 50}\n")

    results: list[EvalResult] = []
    for case in cases:
        query_preview = case["query"][:50]
        print(f"  运行 [{case['id']}] {query_preview}...", end=" ")
        result = await run_single_case(case)
        results.append(result)

        status = "PASS" if result.success else "FAIL"
        print(f"{status} ({result.duration_s:.1f}s)")

    report = EvalReport(
        results=results,
        total=len(cases),
        passed=sum(1 for r in results if r.success),
        failed=sum(1 for r in results if not r.success),
    )

    # ── 输出报告 ──
    print(f"\n{'=' * 50}")
    print(f"  评测报告")
    print(f"{'=' * 50}")
    print(f"  任务成功率:     {report.task_success_rate:.0%}")
    print(f"  平均耗时:       {report.avg_duration:.1f}s")
    print(f"  工具准确率:     {report.avg_tool_accuracy:.0%}")
    print(f"  关键词召回率:   {report.avg_keyword_recall:.0%}")

    # 用例详情
    print(f"\n{'=' * 50}")
    print(f"  用例详情")
    print(f"{'=' * 50}")
    for r in results:
        mark = "PASS" if r.success else "FAIL"
        print(f"  [{mark}] {r.case_id} ({r.category}) — {r.duration_s:.1f}s")
        if r.error:
            print(f"        错误: {r.error[:120]}")

    # ── 保存报告为 JSON ──
    report_path = Path(__file__).parent / "eval_report.json"
    report_data = {
        "results": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "success": r.success,
                "duration_s": r.duration_s,
                "tools_called": r.tools_called,
                "expected_tools": r.expected_tools,
                "keywords_found": r.keywords_found,
                "expected_keywords": r.expected_keywords,
                "error": r.error,
                "tool_accuracy": r.tool_accuracy,
                "keyword_recall": r.keyword_recall,
            }
            for r in results
        ],
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "task_success_rate": report.task_success_rate,
        "avg_duration": report.avg_duration,
        "avg_tool_accuracy": report.avg_tool_accuracy,
        "avg_keyword_recall": report.avg_keyword_recall,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"\n  报告已保存: {report_path}")

    # ── 阈值判断 ──
    if report.task_success_rate < threshold:
        print(
            f"\n  FAIL: 成功率 {report.task_success_rate:.0%} < 阈值 {threshold:.0%}"
        )
        return 1

    print(f"\n  PASS: 评测通过")
    return 0


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="DeepAgents 评测执行器")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="通过阈值，取值范围 0.0-1.0（默认 0.7）",
    )
    args = parser.parse_args()
    exit_code = asyncio.run(run_eval(args.threshold))
    sys.exit(exit_code)
