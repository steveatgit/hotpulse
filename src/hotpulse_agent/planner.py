from __future__ import annotations

from dataclasses import replace

from .config import PolicyConfig
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.parser import parse_planner_decision
from .policy.prompts import PLANNER_SYSTEM_PROMPT, build_planner_create_prompt, build_planner_replan_prompt
from .policy.schemas import PlannerDecision
from .schemas import Plan, SubTask


class RulePlanner:
    TASK_LIBRARY = {
        "scope": ("Identify event scope, timeframe, and aliases", "scope"),
        "updates": ("Collect the newest updates from reliable sources", "recent_updates"),
        "conflicts": ("Check whether key claims are disputed", "conflict_resolution"),
        "timeline": ("Build a compact event timeline", "timeline"),
        "report": ("Generate a grounded intelligence report", "report"),
    }

    def create_plan(self, question: str, event_id: str | None) -> Plan:
        sub_tasks = [
            SubTask(task_id, description, target)
            for task_id, (description, target) in self.TASK_LIBRARY.items()
        ]
        scope = self._infer_scope(question)
        open_questions = ["What happened most recently?", "Which claims remain unverified?"]
        return Plan(
            goal=question,
            event_id=event_id,
            scope=scope,
            sub_tasks=sub_tasks,
            stop_conditions=[
                "At least 4 evidence items collected",
                "At least 2 high-reliability sources collected",
                "Timeline can be reconstructed",
            ],
            open_questions=open_questions,
            confidence=0.6,
            metadata={"decision_source": "rule"},
        )

    def replan(self, plan: Plan, memory: MemoryManager) -> Plan:
        open_questions = list(plan.open_questions)
        if memory.top_entities():
            open_questions.append(f"Can the update be tied more clearly to {memory.top_entities(1)[0]}?")
        if memory.source_diversity() < 2:
            open_questions.append("Need more diverse sources.")
        if memory.conflict_claims():
            open_questions.append("Resolve conflicting claims with independent evidence.")
        if len(memory.evidence) < 4:
            open_questions.append("Need additional evidence before reporting.")
        if not memory.built_timeline and len(memory.evidence) >= 3:
            open_questions.append("Convert evidence into a compact timeline.")
        open_questions = list(dict.fromkeys(open_questions))

        updated_sub_tasks = []
        for task in plan.sub_tasks:
            new_status = task.status
            if task.task_id == "scope" and memory.candidate_docs:
                new_status = "completed"
            elif task.task_id == "updates" and len(memory.evidence) >= 2:
                new_status = "completed"
            elif task.task_id == "conflicts" and not memory.conflict_claims() and len(memory.evidence) >= 3:
                new_status = "completed"
            elif task.task_id == "timeline" and len(memory.evidence) >= 4:
                new_status = "completed"
            elif task.task_id == "report" and self.should_stop(memory):
                new_status = "ready"
            updated_sub_tasks.append(replace(task, status=new_status))

        confidence = min(0.5 + memory.evidence_coverage() * 0.5, 0.95)
        return Plan(
            goal=plan.goal,
            event_id=plan.event_id,
            scope=plan.scope,
            sub_tasks=updated_sub_tasks,
            stop_conditions=plan.stop_conditions,
            open_questions=open_questions[-4:],
            confidence=confidence,
            metadata={"decision_source": "rule"},
        )

    def should_stop(self, memory: MemoryManager) -> bool:
        enough_evidence = len(memory.evidence) >= 4
        enough_sources = len(memory.high_reliability_evidence()) >= 2
        enough_diversity = memory.source_diversity() >= 2
        return enough_evidence and enough_sources and enough_diversity

    def _infer_scope(self, question: str) -> str:
        lowered = question.lower()
        if "24" in lowered or "latest" in lowered or "recent" in lowered:
            return "Focus on the last 24 hours and major unresolved issues."
        return "Focus on major developments, evidence, and unresolved issues."


class HybridPlanner:
    def __init__(self, policy: PolicyConfig, fallback: RulePlanner | None = None) -> None:
        self.policy = policy
        self.fallback = fallback or RulePlanner()
        self.client = LLMClient(policy) if policy.mode in {"llm", "hybrid"} else None

    def create_plan(self, question: str, event_id: str | None) -> Plan:
        if self.policy.mode == "rule":
            return self.fallback.create_plan(question, event_id)
        try:
            decision = self._llm_create_plan(question, event_id)
            return self._decision_to_plan(
                decision,
                goal=question,
                event_id=event_id,
                fallback_plan=self.fallback.create_plan(question, event_id),
                metadata={"decision_source": "llm"},
            )
        except Exception as exc:
            plan = self.fallback.create_plan(question, event_id)
            plan.metadata = {"decision_source": "rule-fallback", "fallback_reason": f"{exc.__class__.__name__}: {exc}"}
            return plan

    def replan(self, plan: Plan, memory: MemoryManager) -> Plan:
        if self.policy.mode == "rule":
            return self.fallback.replan(plan, memory)
        try:
            decision = self._llm_replan(plan, memory)
            return self._decision_to_plan(
                decision,
                goal=plan.goal,
                event_id=plan.event_id,
                fallback_plan=self.fallback.replan(plan, memory),
                metadata={"decision_source": "llm"},
            )
        except Exception as exc:
            fallback_plan = self.fallback.replan(plan, memory)
            fallback_plan.metadata = {
                "decision_source": "rule-fallback",
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
            }
            return fallback_plan

    def should_stop(self, memory: MemoryManager) -> bool:
        return self.fallback.should_stop(memory)

    def _llm_create_plan(self, question: str, event_id: str | None) -> PlannerDecision:
        if self.client is None:
            raise ValueError("LLM client is not initialized.")
        user_prompt = build_planner_create_prompt(question, event_id)
        raw_text = self.client.chat(PLANNER_SYSTEM_PROMPT, user_prompt)
        return parse_planner_decision(raw_text)

    def _llm_replan(self, plan: Plan, memory: MemoryManager) -> PlannerDecision:
        if self.client is None:
            raise ValueError("LLM client is not initialized.")
        user_prompt = build_planner_replan_prompt(plan, memory)
        raw_text = self.client.chat(PLANNER_SYSTEM_PROMPT, user_prompt)
        return parse_planner_decision(raw_text)

    def _decision_to_plan(
        self,
        decision: PlannerDecision,
        *,
        goal: str,
        event_id: str | None,
        fallback_plan: Plan,
        metadata: dict,
    ) -> Plan:
        sub_tasks = self._build_sub_tasks(decision.sub_tasks)
        if not sub_tasks:
            raise ValueError("Planner decision did not produce valid sub_tasks.")
        task_ids = {task.task_id for task in sub_tasks}
        required = set(RulePlanner.TASK_LIBRARY.keys())
        if required - task_ids:
            raise ValueError(f"Planner decision missed required task ids: {sorted(required - task_ids)}")
        return Plan(
            goal=goal,
            event_id=event_id,
            scope=decision.scope or fallback_plan.scope,
            sub_tasks=sub_tasks,
            stop_conditions=decision.stop_conditions or fallback_plan.stop_conditions,
            open_questions=decision.open_questions or fallback_plan.open_questions,
            confidence=max(0.0, min(decision.confidence, 1.0)) or fallback_plan.confidence,
            metadata=metadata,
        )

    def _build_sub_tasks(self, items: list[dict]) -> list[SubTask]:
        built: list[SubTask] = []
        for item in items:
            task_id = str(item.get("task_id", "")).strip()
            if task_id not in RulePlanner.TASK_LIBRARY:
                continue
            default_description, default_target = RulePlanner.TASK_LIBRARY[task_id]
            status = str(item.get("status", "pending")).strip().lower()
            if status not in {"pending", "completed", "ready"}:
                status = "pending"
            built.append(
                SubTask(
                    task_id=task_id,
                    description=str(item.get("description", "")).strip() or default_description,
                    target=str(item.get("target", "")).strip() or default_target,
                    status=status,
                )
            )
        deduped: dict[str, SubTask] = {}
        for task in built:
            deduped[task.task_id] = task
        ordered = []
        for task_id in RulePlanner.TASK_LIBRARY.keys():
            if task_id in deduped:
                ordered.append(deduped[task_id])
        return ordered


Planner = HybridPlanner