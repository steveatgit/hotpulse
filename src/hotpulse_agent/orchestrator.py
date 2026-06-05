from __future__ import annotations

from dataclasses import asdict

from .memory import MemoryManager
from .planner import Planner
from .reflector import Reflector
from .reporting import ReportGenerator
from .router import ToolRouter
from .schemas import AgentResult, AgentState, Observation, Plan, StepTrace
from .tools.registry import ToolRegistry


class HotPulseOrchestrator:
    def __init__(
        self,
        planner: Planner,
        reflector: Reflector,
        router: ToolRouter,
        registry: ToolRegistry,
        reporter: ReportGenerator,
        max_steps: int = 8,
    ) -> None:
        self.planner = planner
        self.reflector = reflector
        self.router = router
        self.registry = registry
        self.reporter = reporter
        self.max_steps = max_steps

    def run(self, question: str, event_id: str | None = None) -> AgentResult:
        memory = MemoryManager()
        memory.working.current_objective = question
        memory.working.current_query = question
        memory.record_query(question)
        memory.working.remaining_steps = self.max_steps

        state = AgentState.INIT
        plan = self.planner.create_plan(question, event_id)
        traces: list[StepTrace] = []
        reflection_decisions: list[dict] = []
        planning_decisions: list[dict] = [dict(plan.metadata)]

        for step_index in range(1, self.max_steps + 1):
            memory.working.remaining_steps = self.max_steps - step_index
            if state in {AgentState.INIT, AgentState.REPLANNING}:
                state = AgentState.PLANNING

            choice = self.router.choose(state, plan, memory)
            tool_name, reason = choice.tool_name, choice.reason
            observation = self._execute_tool(tool_name, question, event_id, plan, memory)
            traces.append(
                StepTrace(
                    step_index=step_index,
                    state=state.value,
                    tool_name=tool_name,
                    reason=reason,
                    observation=observation.summary,
                    metadata={**choice.metadata, **observation.payload.get("trace_metadata", {})},
                )
            )

            self._apply_observation(observation, memory)

            if tool_name == "search_web":
                state = AgentState.SEARCHING
            elif tool_name == "fetch_page":
                state = AgentState.READING
            elif tool_name == "extract_evidence":
                state = AgentState.EVIDENCE_EXTRACTING
            elif tool_name == "build_timeline":
                state = AgentState.REFLECTING

            plan = self.planner.replan(plan, memory)
            planning_decisions.append(dict(plan.metadata))
            memory.unresolved_questions = list(plan.open_questions)
            reflection = self.reflector.assess(plan, memory)
            reflection_decisions.append(
                {
                    "decision_source": reflection.metadata.get("decision_source", "unknown"),
                    "should_replan": reflection.should_replan,
                    "should_rewrite_query": reflection.should_rewrite_query,
                    "should_continue": reflection.should_continue,
                    "next_query": reflection.next_query,
                    "notes": reflection.notes,
                    "metadata": reflection.metadata,
                }
            )
            for note in reflection.notes:
                memory.add_reflection("reflection", note)
            if reflection.should_rewrite_query and reflection.next_query != memory.working.current_query:
                memory.working.current_query = reflection.next_query
                memory.record_query(reflection.next_query)
                state = AgentState.REPLANNING

            if self.planner.should_stop(memory):
                state = AgentState.REPORTING
                break

            if step_index >= self.max_steps - 1 and not self.planner.should_stop(memory):
                memory.add_reflection("budget", "Stopped because step budget was exhausted.")
                state = AgentState.REPORTING
                break

            if tool_name == "build_timeline" and len(memory.evidence) < 4:
                state = AgentState.REPLANNING

        report = self.reporter.generate(question, memory)
        final_state = AgentState.DONE if memory.evidence else AgentState.FAILED
        metrics = {
            "evidence_count": len(memory.evidence),
            "source_diversity": memory.source_diversity(),
            "coverage": memory.evidence_coverage(),
            "high_reliability_evidence": len(memory.high_reliability_evidence()),
            "memory_summary": memory.summary(),
            "plan_confidence": plan.confidence,
            "plan_metadata": plan.metadata,
            "planning_decisions": planning_decisions,
            "query_history": memory.working.query_history,
            "router_decision_sources": [trace.metadata.get("decision_source", "unknown") for trace in traces],
            "reflection_decisions": reflection_decisions,
            "report_decision": getattr(self.reporter, "last_metadata", {}),
            "policy_call_history": getattr(getattr(self.reporter, "policy", None), "call_history", []),
            "policy_circuit_open_reason": getattr(getattr(self.reporter, "policy", None), "circuit_open_reason", ""),
            "reflections": [asdict(note) for note in memory.reflections],
            "sub_tasks": [asdict(task) for task in plan.sub_tasks],
        }
        return AgentResult(plan=plan, report=report, state=final_state, traces=traces, metrics=metrics)

    def _execute_tool(
        self,
        tool_name: str,
        question: str,
        event_id: str | None,
        plan: Plan,
        memory: MemoryManager,
    ) -> Observation:
        if tool_name == "search_web":
            tool = self.registry.get(tool_name)
            docs = tool.run(query=memory.working.current_query, event_id=event_id)
            summary = f"Retrieved {len(docs)} candidate documents."
            return Observation(tool_name=tool_name, summary=summary, payload={"docs": docs})

        if tool_name == "fetch_page":
            tool = self.registry.get(tool_name)
            fetch_result = tool.run(docs=memory.next_fetch_candidates(limit=3), fetched_doc_ids=set(memory.fetched_docs))
            if fetch_result is None:
                return Observation(tool_name=tool_name, summary="No unread document remained.", payload={})
            doc = fetch_result["doc"]
            fetch_mode = fetch_result.get("fetch_mode", "unknown")
            summary = f"Fetched {doc.doc_id} from {doc.source} via {fetch_mode}."
            return Observation(
                tool_name=tool_name,
                summary=summary,
                payload={
                    "doc": doc,
                    "trace_metadata": {"fetch_mode": fetch_mode},
                },
            )

        if tool_name == "extract_evidence":
            tool = self.registry.get(tool_name)
            unread_docs = [doc for doc_id, doc in memory.fetched_docs.items() if doc_id not in {e.doc_id for e in memory.evidence}]
            if not unread_docs:
                return Observation(tool_name=tool_name, summary="No fetched document needs extraction.", payload={})
            doc = unread_docs[0]
            evidence = tool.run(doc=doc)
            summary = f"Extracted {len(evidence)} evidence items from {doc.doc_id}."
            return Observation(tool_name=tool_name, summary=summary, payload={"evidence": evidence})

        if tool_name == "build_timeline":
            tool = self.registry.get(tool_name)
            timeline = tool.run(evidence=memory.evidence)
            summary = f"Built timeline with {len(timeline)} time anchors."
            return Observation(tool_name=tool_name, summary=summary, payload={"timeline": timeline})

        if tool_name == "final_report":
            relevant = memory.retrieve_relevant_evidence(query=question, limit=3)
            summary = f"Prepared final synthesis from {len(relevant)} high-salience evidence items."
            return Observation(tool_name=tool_name, summary=summary, payload={"relevant_evidence": relevant})

        return Observation(
            tool_name=tool_name,
            summary=f"Unsupported tool selected: {tool_name}.",
            payload={"question": question, "plan_goal": plan.goal},
        )

    def _apply_observation(self, observation: Observation, memory: MemoryManager) -> None:
        memory.working.last_tool = observation.tool_name
        if observation.tool_name == "search_web":
            docs = observation.payload.get("docs", [])
            memory.add_candidates(docs)
            if not docs:
                memory.add_reflection("search_failure", "Search returned no candidates.")
        elif observation.tool_name == "fetch_page":
            doc = observation.payload.get("doc")
            if doc is not None:
                memory.add_fetched(doc)
            else:
                memory.add_reflection("fetch_failure", "No more documents to fetch.")
        elif observation.tool_name == "extract_evidence":
            evidence = observation.payload.get("evidence", [])
            memory.add_evidence(evidence)
            if not evidence:
                memory.add_reflection("extraction_failure", "No evidence was extracted from the fetched page.")
        elif observation.tool_name == "build_timeline":
            timeline = observation.payload.get("timeline", {})
            if not timeline:
                memory.add_reflection("timeline_failure", "Timeline builder returned no result.")
            else:
                memory.built_timeline = timeline
