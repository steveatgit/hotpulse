from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentState(str, Enum):
    INIT = "INIT"
    PLANNING = "PLANNING"
    SEARCHING = "SEARCHING"
    READING = "READING"
    EVIDENCE_EXTRACTING = "EVIDENCE_EXTRACTING"
    REFLECTING = "REFLECTING"
    REPLANNING = "REPLANNING"
    REPORTING = "REPORTING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class SearchDocument:
    doc_id: str
    event_id: str | None
    title: str
    source: str
    source_type: str
    published_at: str
    reliability: float
    url: str
    content: str
    claims: list[str]
    entities: list[str]
    tags: list[str]


@dataclass
class SubTask:
    task_id: str
    description: str
    target: str
    status: str = "pending"


@dataclass
class Plan:
    goal: str
    event_id: str | None
    scope: str
    sub_tasks: list[SubTask]
    stop_conditions: list[str]
    open_questions: list[str] = field(default_factory=list)
    confidence: float = 0.5
    metadata: dict = field(default_factory=dict)


@dataclass
class Observation:
    tool_name: str
    summary: str
    payload: dict


@dataclass
class Evidence:
    doc_id: str
    title: str
    source: str
    source_type: str
    published_at: str
    claim: str
    supporting_text: str
    reliability: float


@dataclass
class ReflectionNote:
    category: str
    message: str


@dataclass
class StepTrace:
    step_index: int
    state: str
    tool_name: str
    reason: str
    observation: str
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    plan: Plan
    report: str
    state: AgentState
    traces: list[StepTrace]
    metrics: dict