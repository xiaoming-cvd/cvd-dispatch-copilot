"""统一的校验错误码与问题描述。

阶段 0 约定：所有输入校验问题都以 ValidationIssue 形式返回，
包含规则 ID（code）、定位路径（path）与人可读解释（message）。
不得静默吞错；页面与 CLI 都直接展示这些条目。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationIssue:
    """一条输入校验问题。

    code 即规则 ID，命名规则 E_<域>_<问题>；path 用 JSON 路径定位，
    例如 "orders[2].due_s"。severity 为 "error" 时输入被拒绝。
    """

    code: str
    message: str
    path: str = ""
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ValidationReport:
    """一组校验问题的汇总。ok 为 True 表示没有任何 error 级问题。"""

    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}
