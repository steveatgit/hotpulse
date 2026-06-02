from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTool(ABC):
    name: str

    @abstractmethod
    def run(self, **kwargs):
        raise NotImplementedError