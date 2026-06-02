from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RouterDecision:
    selected_tool: str
    reason: str
    confidence: float = 0.0


@dataclass
class ReflectorDecision:
    should_replan: bool
    should_rewrite_query: bool
    should_continue: bool
    next_query: str
    notes: list[str]
    confidence: float = 0.0


@dataclass
class PlannerDecision:
    scope: str
    sub_tasks: list[dict]
    stop_conditions: list[str]
    open_questions: list[str]
    confidence: float = 0.0