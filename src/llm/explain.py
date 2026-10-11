"""结果解释：只引用 SolveResult 里已算出的 metrics/diff/validation。

红线（任务书阶段 4）：解释不得编造任何数字或结论——每个数值必须来自
求解结果对象的既有字段，字段缺失就省略对应行，不得用估算值顶替。
"""
from __future__ import annotations

from ..domain.models import SolveResult


def explain_result(result: SolveResult) -> str:
    """把求解结果渲染为中文解释文本（仅引用已算字段）。"""
    lines: list[str] = [f"求解状态：{result.status.value}"]

    metrics = result.metrics if isinstance(result.metrics, dict) else {}
    if "total_travel_time_s" in metrics:
        lines.append(f"总行驶时间：{metrics['total_travel_time_s']} 秒")
    if "vehicle_count" in metrics:
        lines.append(f"使用车辆数：{metrics['vehicle_count']}")
    if "elapsed_solve_ms" in metrics:
        lines.append(f"求解耗时：{metrics['elapsed_solve_ms']} 毫秒")

    diff = result.diff if isinstance(result.diff, dict) else {}
    if "vehicle_changes" in diff:
        lines.append(f"换车任务数：{diff['vehicle_changes']}")
    if "adjacent_breaks" in diff:
        lines.append(f"相邻关系破坏数：{diff['adjacent_breaks']}")
    changes = diff.get("changes")
    if isinstance(changes, (list, tuple)):
        lines.append(f"逐任务变更条目：{len(changes)} 条")

    validation = result.validation if isinstance(result.validation, dict) else {}
    if "ok" in validation:
        lines.append(f"独立校验：{'通过' if validation['ok'] else '未通过'}")

    for w in result.warnings:
        lines.append(f"警告：{w}")
    for a in result.assumptions:
        lines.append(f"假设：{a}")
    return "\n".join(lines)
