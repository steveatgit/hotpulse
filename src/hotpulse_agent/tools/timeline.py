"""
Timeline Tool for HotPulse Agent
"""
from typing import List, Dict, Any
from dataclasses import dataclass, field


@dataclass
class TimelineEvent:
    step: int
    action: str
    agent: str
    detail: str
    timestamp: str = None


class Timeline:
    def __init__(self):
        self.events: List[TimelineEvent] = []

    def add_event(self, step: int, action: str, agent: str, detail: str):
        event = TimelineEvent(
            step=step,
            action=action,
            agent=agent,
            detail=detail
        )
        self.events.append(event)

    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "step": e.step,
                "action": e.action,
                "agent": e.agent,
                "detail": e.detail,
                "timestamp": e.timestamp
            }
            for e in self.events
        ]


class BuildTimelineTool:
    # 必须加这个 name！
    name = "build_timeline"

    def __init__(self):
        self.timeline = Timeline()

    def add_step(self, step: int, action: str, agent: str, detail: str):
        self.timeline.add_event(step, action, agent, detail)

    def build(self) -> str:
        return "\n".join([
            f"Step {e.step}: {e.action} | Agent: {e.agent} | Detail: {e.detail}"
            for e in self.timeline.events
        ])

    def get_events(self):
        return self.timeline.events