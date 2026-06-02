from __future__ import annotations

import json
import urllib.request

from ..config import PolicyConfig


class LLMClient:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.config.base_url or not self.config.api_key or not self.config.model:
            raise ValueError("LLM policy config is incomplete. base_url, api_key, and model are required.")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps(
            {
                "model": self.config.model,
                "temperature": self.config.temperature,
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
        with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))

        choices = body.get("choices", [])
        if not choices:
            raise ValueError("LLM response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return str(content)