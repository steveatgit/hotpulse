from __future__ import annotations

from collections import defaultdict

from ..schemas import Evidence
from .base import BaseTool


class BuildTimelineTool(BaseTool):
    name = "build_timeline"

    def run(self, evidence: list[Evidence]) -> dict[str, list[str]]:
        timeline: dict[str, list[str]] = defaultdict(list)
        for item in sorted(evidence, key=lambda item: item.published_at):
            timeline[item.published_at].append(f"{item.source}: {item.claim}")
        return dict(timeline)
