"""评估指标计算。"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvalResult:
    """单个用例的评估结果。"""
    case_id: str
    category: str
    success: bool
    duration_s: float
    tools_called: list[str]
    expected_tools: list[str]
    keywords_found: list[str]
    expected_keywords: list[str]
    error: Optional[str] = None

    @property
    def tool_accuracy(self) -> float:
        """工具调用准确率 — 至少调用了一个预期工具。"""
        if not self.expected_tools:
            return 1.0
        matched = set(self.expected_tools) & set(self.tools_called)
        return len(matched) / len(self.expected_tools) if self.expected_tools else 0.0

    @property
    def keyword_recall(self) -> float:
        """关键词召回率 — 响应中包含的关键词比例。"""
        if not self.expected_keywords:
            return 1.0
        matched = [kw for kw in self.expected_keywords if kw in self.keywords_found]
        return len(matched) / len(self.expected_keywords)


@dataclass
class EvalReport:
    """一次评估的完整报告。"""
    results: list[EvalResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def task_success_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def avg_duration(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.duration_s for r in self.results) / len(self.results)

    @property
    def avg_tool_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.tool_accuracy for r in self.results) / len(self.results)

    @property
    def avg_keyword_recall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.keyword_recall for r in self.results) / len(self.results)


def compare_reports(current: EvalReport, baseline: EvalReport) -> dict:
    """对比两个报告，检测退化。"""
    changes = {}
    changes["task_success_rate"] = current.task_success_rate - baseline.task_success_rate
    changes["avg_duration"] = current.avg_duration - baseline.avg_duration
    changes["avg_tool_accuracy"] = current.avg_tool_accuracy - baseline.avg_tool_accuracy
    changes["avg_keyword_recall"] = current.avg_keyword_recall - baseline.avg_keyword_recall

    # 退化阈值
    regressions = []
    if changes["task_success_rate"] < -0.1:
        regressions.append(f"任务成功率下降 {abs(changes['task_success_rate']):.0%}")
    if changes["avg_tool_accuracy"] < -0.1:
        regressions.append(f"工具准确率下降 {abs(changes['avg_tool_accuracy']):.0%}")
    if changes["avg_keyword_recall"] < -0.1:
        regressions.append(f"关键词召回率下降 {abs(changes['avg_keyword_recall']):.0%}")

    return {"changes": changes, "regressions": regressions}
