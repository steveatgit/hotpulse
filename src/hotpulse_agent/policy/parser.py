from __future__ import annotations

import json
import re

from .schemas import PlannerDecision, ReflectorDecision, RouterDecision


def parse_router_decision(raw_text: str) -> RouterDecision:
    text = raw_text.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    payload = json.loads(text)
    return RouterDecision(
        selected_tool=str(payload["selected_tool"]).strip(),
        reason=str(payload["reason"]).strip(),
        confidence=float(payload.get("confidence", 0.0)),
    )


def parse_reflector_decision(raw_text: str) -> ReflectorDecision:
    text = raw_text.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    payload = json.loads(text)
    notes = payload.get("notes", [])
    if not isinstance(notes, list):
        notes = [str(notes)]
    return ReflectorDecision(
        should_replan=bool(payload.get("should_replan", False)),
        should_rewrite_query=bool(payload.get("should_rewrite_query", False)),
        should_continue=bool(payload.get("should_continue", True)),
        next_query=str(payload.get("next_query", "")).strip(),
        notes=[str(item).strip() for item in notes if str(item).strip()],
        confidence=float(payload.get("confidence", 0.0)),
    )


def parse_planner_decision(raw_text: str) -> PlannerDecision:
    text = raw_text.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    payload = json.loads(text)
    sub_tasks = payload.get("sub_tasks", [])
    stop_conditions = payload.get("stop_conditions", [])
    open_questions = payload.get("open_questions", [])
    if not isinstance(sub_tasks, list):
        raise ValueError("sub_tasks must be a list")
    if not isinstance(stop_conditions, list):
        stop_conditions = [str(stop_conditions)]
    if not isinstance(open_questions, list):
        open_questions = [str(open_questions)]
    return PlannerDecision(
        scope=str(payload.get("scope", "")).strip(),
        sub_tasks=sub_tasks,
        stop_conditions=[str(item).strip() for item in stop_conditions if str(item).strip()],
        open_questions=[str(item).strip() for item in open_questions if str(item).strip()],
        confidence=float(payload.get("confidence", 0.0)),
    )