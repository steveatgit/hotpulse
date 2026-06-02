from __future__ import annotations

from dataclasses import dataclass, field
import re

from .config import PolicyConfig
from .language import extract_chinese_search_hints, extract_chinese_terms, extract_latin_terms, prefer_chinese
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.parser import parse_reflector_decision
from .policy.prompts import REFLECTOR_SYSTEM_PROMPT, build_reflector_user_prompt
from .policy.schemas import ReflectorDecision
from .schemas import Plan


@dataclass
class ReflectionResult:
    should_replan: bool
    should_rewrite_query: bool
    should_continue: bool
    next_query: str
    notes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class RuleReflector:
    def assess(self, plan: Plan, memory: MemoryManager) -> ReflectionResult:
        notes: list[str] = []
        should_rewrite_query = False
        should_replan = False
        next_query = memory.working.current_query
        pending_extraction = self._has_pending_extraction(memory)
        unread_candidates = self._has_unread_candidates(memory)

        if len(memory.evidence) < 2:
            notes.append("Evidence remains thin; keep searching.")

        if memory.source_diversity() < 2 and len(memory.evidence) >= 2 and not pending_extraction and not unread_candidates:
            notes.append("Source diversity is weak; widen the source pool.")
            should_rewrite_query = True

        conflicts = memory.conflict_claims()
        if conflicts:
            notes.extend(conflicts)
            should_replan = True
            should_rewrite_query = True

        recent_failures = " ".join(memory.recent_reflection_messages()).lower()
        if ("no candidates" in recent_failures or "no more documents" in recent_failures) and not pending_extraction and not unread_candidates:
            notes.append("Current retrieval path is saturated; rewrite the query.")
            should_rewrite_query = True

        if should_rewrite_query:
            next_query = self._rewrite_query(plan.goal, memory)
            if next_query != memory.working.current_query:
                notes.append(f"Rewrite query to: {next_query}")

        return ReflectionResult(
            should_replan=should_replan or should_rewrite_query,
            should_rewrite_query=should_rewrite_query,
            should_continue=True,
            next_query=next_query,
            notes=notes,
            metadata={"decision_source": "rule"},
        )

    def _rewrite_query(self, goal: str, memory: MemoryManager) -> str:
        if prefer_chinese(goal):
            retained = extract_latin_terms(goal, limit=4)
            retained.extend(extract_chinese_search_hints(goal))
            retained.extend(extract_chinese_terms(goal, limit=2))
            expansions = memory.top_entities(limit=3)
            if memory.conflict_claims():
                expansions.extend(["官方通报", "调查进展", "说法差异"])
            elif memory.source_diversity() < 2:
                expansions.extend(["官方", "最新通报", "调查"])
            else:
                expansions.extend(["最新进展", "现场", "调查"])
            merged: list[str] = []
            for token in retained + expansions:
                token = str(token).strip()
                if not token:
                    continue
                if token not in merged:
                    merged.append(token)
            return " ".join(merged[:8])

        retained = extract_latin_terms(goal, limit=8)
        expansions = memory.top_entities(limit=3)

        if memory.conflict_claims():
            expansions.extend(["conflict", "verification"])
        elif memory.source_diversity() < 2:
            expansions.extend(["official", "investigation"])
        else:
            expansions.extend(["latest", "update"])

        merged: list[str] = []
        for token in retained + expansions:
            lowered = token.lower()
            if lowered not in {item.lower() for item in merged}:
                merged.append(token)
        return " ".join(merged[:12])

    def _has_pending_extraction(self, memory: MemoryManager) -> bool:
        extracted_doc_ids = {item.doc_id for item in memory.evidence}
        return any(doc_id not in extracted_doc_ids for doc_id in memory.fetched_docs)

    def _has_unread_candidates(self, memory: MemoryManager) -> bool:
        return any(doc.doc_id not in memory.fetched_docs for doc in memory.candidate_docs)


class HybridReflector:
    def __init__(self, policy: PolicyConfig, fallback: RuleReflector | None = None) -> None:
        self.policy = policy
        self.fallback = fallback or RuleReflector()
        self.client = LLMClient(policy) if policy.mode in {"llm", "hybrid"} else None

    def assess(self, plan: Plan, memory: MemoryManager) -> ReflectionResult:
        if self.policy.mode == "rule":
            return self.fallback.assess(plan, memory)

        try:
            decision = self._llm_assess(plan, memory)
            result = ReflectionResult(
                should_replan=decision.should_replan,
                should_rewrite_query=decision.should_rewrite_query,
                should_continue=decision.should_continue,
                next_query=decision.next_query or memory.working.current_query,
                notes=decision.notes,
                metadata={
                    "decision_source": "llm",
                    "reflector_confidence": decision.confidence,
                },
            )
            return self._apply_policy_gates(result, memory)
        except Exception as exc:
            fallback_result = self.fallback.assess(plan, memory)
            fallback_result.metadata = {
                **fallback_result.metadata,
                "decision_source": "rule-fallback",
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
            }
            return fallback_result

    def _llm_assess(self, plan: Plan, memory: MemoryManager) -> ReflectorDecision:
        if self.client is None:
            raise ValueError("LLM client is not initialized.")
        user_prompt = build_reflector_user_prompt(plan, memory, memory.working.current_query)
        raw_text = self.client.chat(REFLECTOR_SYSTEM_PROMPT, user_prompt)
        return parse_reflector_decision(raw_text)

    def _apply_policy_gates(self, result: ReflectionResult, memory: MemoryManager) -> ReflectionResult:
        extracted_doc_ids = {item.doc_id for item in memory.evidence}
        pending_extraction = any(doc_id not in extracted_doc_ids for doc_id in memory.fetched_docs)
        unread_candidates = any(doc.doc_id not in memory.fetched_docs for doc in memory.candidate_docs)
        metadata = dict(result.metadata)
        if (pending_extraction or unread_candidates) and result.should_rewrite_query:
            metadata["gate_adjustment"] = (
                "rewrite_blocked_due_to_pending_extraction"
                if pending_extraction
                else "rewrite_blocked_due_to_unread_candidates"
            )
            result = ReflectionResult(
                should_replan=result.should_replan,
                should_rewrite_query=False,
                should_continue=result.should_continue,
                next_query=memory.working.current_query,
                notes=result.notes,
                metadata=metadata,
            )
        return result


Reflector = HybridReflector