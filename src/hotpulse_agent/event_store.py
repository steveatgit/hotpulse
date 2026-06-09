from __future__ import annotations

from dataclasses import asdict
import json
import re
from pathlib import Path

from .memory import MemoryManager
from .schemas import StepTrace


class EventArchiveStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def archive_id(self, question: str, event_id: str | None) -> str:
        if event_id:
            return _safe_slug(event_id)
        return _safe_slug(question)[:80]

    def load(self, archive_id: str) -> dict:
        path = self._path(archive_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}
        return payload

    def save(
        self,
        *,
        archive_id: str,
        question: str,
        event_id: str | None,
        memory: MemoryManager,
        traces: list[StepTrace],
        report: str,
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "archive_id": archive_id,
            "question": question,
            "event_id": event_id,
            "snapshot": asdict(memory.incremental_snapshot) if memory.incremental_snapshot else {},
            "evidence": [asdict(item) for item in memory.evidence],
            "timeline_events": [asdict(item) for item in memory.built_timeline],
            "event_clusters": [asdict(item) for item in memory.event_clusters],
            "source_assessments": [asdict(item) for item in memory.source_assessments],
            "query_history": list(memory.working.query_history),
            "reflections": [asdict(item) for item in memory.reflections],
            "trace": [asdict(item) for item in traces],
            "report": report,
        }
        path = self._path(archive_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path

    def _path(self, archive_id: str) -> Path:
        return self.root / f"{archive_id}.json"


def _safe_slug(value: str) -> str:
    parts = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", value.lower())
    return "-".join(parts[:16]) or "event"
