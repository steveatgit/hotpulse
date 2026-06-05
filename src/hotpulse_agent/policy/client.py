from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from ..config import PolicyConfig


class LLMClient:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self.last_metadata: dict = {}

    def chat(self, system_prompt: str, user_prompt: str, *, component: str = "policy") -> str:
        if not self.config.base_url or not self.config.api_key or not self.config.model:
            raise ValueError("LLM policy config is incomplete. base_url, api_key, and model are required.")
        if self.config.circuit_open_reason:
            raise RuntimeError(f"LLM circuit breaker is open: {self.config.circuit_open_reason}")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        timeout = self.config.timeout_for(component)
        max_tokens = self.config.max_tokens_for(component)
        payload = json.dumps(
            {
                "model": self.config.model,
                "temperature": self.config.temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        started_at = time.monotonic()
        metadata = {
            "component": component,
            "model": self.config.model,
            "timeout": timeout,
            "max_tokens": max_tokens,
        }
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            metadata.update(
                {
                    "latency_ms": round((time.monotonic() - started_at) * 1000),
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                }
            )
            self._record_failure(metadata, exc)
            raise

        metadata.update({"latency_ms": round((time.monotonic() - started_at) * 1000), "status": "ok"})
        self.config.consecutive_timeouts = 0
        self.last_metadata = metadata
        self.config.call_history.append(metadata)

        choices = body.get("choices", [])
        if not choices:
            raise ValueError("LLM response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return str(content)

    def _record_failure(self, metadata: dict, exc: Exception) -> None:
        is_timeout = _is_timeout_error(exc)
        if is_timeout:
            self.config.consecutive_timeouts += 1
            metadata["timeout_count"] = self.config.consecutive_timeouts
            if self.config.consecutive_timeouts >= self.config.circuit_breaker_threshold:
                self.config.circuit_open_reason = (
                    f"{self.config.consecutive_timeouts} consecutive LLM timeouts; "
                    "falling back to rule policy for this run"
                )
                metadata["circuit_open_reason"] = self.config.circuit_open_reason
        self.last_metadata = metadata
        self.config.call_history.append(metadata)


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and exc.reason:
        reason = exc.reason
        return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    return "timed out" in str(exc).lower()
