from __future__ import annotations

import re

from ..schemas import Evidence, SearchDocument
from .base import BaseTool


class ExtractEvidenceTool(BaseTool):
    name = "extract_evidence"

    def run(self, doc: SearchDocument) -> list[Evidence]:
        claims = doc.claims or self._derive_claims(doc.content)
        return [
            Evidence(
                doc_id=doc.doc_id,
                title=doc.title,
                source=doc.source,
                source_type=doc.source_type,
                published_at=doc.published_at,
                claim=claim,
                supporting_text=doc.content[:240],
                reliability=doc.reliability,
            )
            for claim in claims
        ]

    def _derive_claims(self, content: str, limit: int = 3) -> list[str]:
        cleaned = re.sub(r"\s+", " ", content).strip()
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        claims = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 40:
                continue
            claims.append(sentence[:220].rstrip())
            if len(claims) >= limit:
                break
        return claims