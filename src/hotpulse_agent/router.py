from __future__ import annotations

from dataclasses import dataclass, field

from .config import PolicyConfig
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.parser import parse_router_decision
from .policy.prompts import ROUTER_SYSTEM_PROMPT, build_router_user_prompt
from .policy.schemas import RouterDecision
from .schemas import AgentState, Plan


@dataclass
class ToolChoice:
    tool_name: str
    reason: str
    metadata: dict = field(default_factory=dict)


class RuleToolRouter:
    def choose(self, state: AgentState, plan: Plan, memory: MemoryManager) -> ToolChoice:
        if state in {AgentState.INIT, AgentState.PLANNING, AgentState.REPLANNING}:
            return ToolChoice("search_web", "Need candidate documents before deeper analysis.", {"decision_source": "rule"})

        if state == AgentState.SEARCHING:
            if not memory.candidate_docs:
                return ToolChoice("search_web", "No candidate documents collected yet.", {"decision_source": "rule"})
            return ToolChoice("fetch_page", "Search results are available, fetch top unread source.", {"decision_source": "rule"})

        if state == AgentState.READING:
            return ToolChoice("extract_evidence", "Fetched page content should be normalized into evidence.", {"decision_source": "rule"})

        if state == AgentState.EVIDENCE_EXTRACTING:
            if len(memory.evidence) >= 4 and not memory.built_timeline:
                return ToolChoice("build_timeline", "Evidence is sufficient to structure a timeline.", {"decision_source": "rule"})
            return ToolChoice("search_web", "Need more evidence before timeline construction.", {"decision_source": "rule"})

        if state == AgentState.REFLECTING:
            if len(memory.evidence) < 4 or memory.source_diversity() < 2:
                return ToolChoice("search_web", "Coverage is not strong enough yet.", {"decision_source": "rule"})
            if not memory.built_timeline:
                return ToolChoice("build_timeline", "Coverage is acceptable; structure the event chronology.", {"decision_source": "rule"})
            return ToolChoice("final_report", "Evidence and timeline are ready for reporting.", {"decision_source": "rule"})

        if state == AgentState.REPORTING:
            return ToolChoice("final_report", "All conditions are satisfied for reporting.", {"decision_source": "rule"})

        return ToolChoice("search_web", "Fallback to search.", {"decision_source": "rule"})


class HybridToolRouter:
    def __init__(self, policy: PolicyConfig, fallback: RuleToolRouter | None = None) -> None:
        self.policy = policy
        self.fallback = fallback or RuleToolRouter()
        self.client = LLMClient(policy) if policy.mode in {"llm", "hybrid"} else None

    def choose(self, state: AgentState, plan: Plan, memory: MemoryManager) -> ToolChoice:
        if self.policy.mode == "rule":
            return self.fallback.choose(state, plan, memory)

        allowed_tools = self._allowed_tools(state, memory)
        try:
            llm_choice = self._llm_choose(state, plan, memory, allowed_tools)
            if llm_choice.selected_tool not in allowed_tools:
                raise ValueError(
                    f"Selected tool {llm_choice.selected_tool} violates state/policy constraints. allowed={allowed_tools}"
                )
            return ToolChoice(
                llm_choice.selected_tool,
                llm_choice.reason,
                {
                    "decision_source": "llm",
                    "router_confidence": llm_choice.confidence,
                    "allowed_tools": allowed_tools,
                },
            )
        except Exception as exc:
            fallback_choice = self.fallback.choose(state, plan, memory)
            fallback_choice.metadata = {
                **fallback_choice.metadata,
                "decision_source": "rule-fallback",
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
                "allowed_tools": allowed_tools,
            }
            return fallback_choice

    def _llm_choose(
        self,
        state: AgentState,
        plan: Plan,
        memory: MemoryManager,
        allowed_tools: list[str],
    ) -> RouterDecision:
        if self.client is None:
            raise ValueError("LLM client is not initialized.")
        user_prompt = build_router_user_prompt(state, plan, memory, allowed_tools)
        raw_text = self.client.chat(ROUTER_SYSTEM_PROMPT, user_prompt)
        return parse_router_decision(raw_text)

    def _allowed_tools(self, state: AgentState, memory: MemoryManager) -> list[str]:
        if state in {AgentState.INIT, AgentState.PLANNING, AgentState.REPLANNING}:
            return ["search_web"]

        if state == AgentState.SEARCHING:
            return ["fetch_page"] if memory.candidate_docs else ["search_web"]

        if state == AgentState.READING:
            return ["extract_evidence"]

        if state == AgentState.EVIDENCE_EXTRACTING:
            if len(memory.evidence) >= 4 and not memory.built_timeline:
                return ["build_timeline", "search_web"]
            return ["search_web"]

        if state == AgentState.REFLECTING:
            if len(memory.evidence) < 4 or memory.source_diversity() < 2:
                return ["search_web"]
            if not memory.built_timeline:
                return ["build_timeline", "search_web"]
            return ["final_report", "search_web"]

        if state == AgentState.REPORTING:
            return ["final_report"]

        return ["search_web"]


ToolRouter = HybridToolRouter