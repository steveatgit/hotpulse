from __future__ import annotations

import re

from ..schemas import Evidence, SearchDocument
from .base import BaseTool


class ExtractEvidenceTool(BaseTool):
    name = "extract_evidence"
    BOILERPLATE_PATTERNS = [
        r"\bcookies?\b",
        r"\btracker preferences?\b",
        r"\bprivacy choices?\b",
        r"\bsubscriber agreement\b",
        r"\bcopyright law\b",
        r"\bdow jones reprints\b",
        r"\bwhat to read next\b",
        r"\bmost popular news\b",
        r"\bmost popular opinion\b",
        r"\bsale[\"”]? or [\"“]?sharing\b",
        r"\bdeliver ads\b",
        r"\bmanage your tracker\b",
        r"\bopt in or out\b",
        r"\bfully opt out\b",
        r"\bprivacy policy\b",
        r"\bnot subscribed\b",
        r"\bsign up here\b",
        r"\byour rights\b",
    ]

    def run(self, doc: SearchDocument) -> list[Evidence]:
        claims = doc.claims or self._derive_claims(doc.content)
        evidence = []
        for index, claim in enumerate(claims, start=1):
            evidence.append(Evidence(
                doc_id=doc.doc_id,
                title=doc.title,
                source=doc.source,
                source_type=doc.source_type,
                published_at=doc.published_at,
                claim=claim,
                supporting_text=self._supporting_text(doc.content, claim),
                reliability=doc.reliability,
                evidence_id=f"{doc.doc_id}#{index}",
                url=doc.url,
                entities=list(doc.entities),
                tags=list(doc.tags),
                claim_type=self._claim_type(claim),
            ))
        return evidence

    def _derive_claims(self, content: str, limit: int = 3) -> list[str]:
        cleaned = re.sub(r"\s+", " ", content).strip()
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        claims = []
        for sentence in sentences:
            sentence = self._clean_sentence(sentence)
            if len(sentence) < 40:
                continue
            if self._is_boilerplate(sentence):
                continue
            claims.append(sentence[:220].rstrip())
            if len(claims) >= limit:
                break
        return claims

    def _is_boilerplate(self, sentence: str) -> bool:
        lowered = sentence.lower()
        return any(re.search(pattern, lowered) for pattern in self.BOILERPLATE_PATTERNS)

    def _clean_sentence(self, sentence: str) -> str:
        sentence = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", sentence)
        sentence = re.sub(r"https?://\S+", "", sentence)
        sentence = re.sub(r"#+\s*", "", sentence)
        return re.sub(r"\s+", " ", sentence).strip()

    def _supporting_text(self, content: str, claim: str, max_chars: int = 260) -> str:
        cleaned = re.sub(r"\s+", " ", content).strip()
        if not cleaned:
            return claim[:max_chars]
        anchor = claim[:80].strip()
        index = cleaned.find(anchor) if anchor else -1
        if index < 0:
            return cleaned[:max_chars]
        start = max(index - 40, 0)
        return cleaned[start : start + max_chars].strip()

    def _claim_type(self, claim: str) -> str:
        lowered = claim.lower()
        patterns = {
            "rumor_control": r"rumou?r|disinformation|misinformation|谣言|传言|辟谣",
            "casualty": r"fatal|death|injur|casualt|hospital|surgery|alive|死亡|伤亡|受伤|医院|手术|生还",
            "cause": r"cause|investigat|brake|maintenance|原因|调查|故障",
            "response": r"response|announc|statement|回应|声明|处置",
            "impact": r"impact|closed|resumed|delay|影响|关闭|恢复|延误",
            "official_update": r"official|authority|police|agency|regulator|通报|官方|部门",
        }
        for claim_type, pattern in patterns.items():
            if re.search(pattern, lowered):
                return claim_type
        return "update"
