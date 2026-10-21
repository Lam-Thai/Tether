"""`ToolRegistry`: the backend operations the model is allowed to request.

Empty in Slice 4. Slice 6 registers the first tool (`search_faq`). The engine
treats any requested tool that isn't registered here as invalid model
output, so an unregistered name can never reach an executor.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from integrations.llm.port import ToolDeclaration


@dataclass(frozen=True)
class ToolRegistry:
    _tools: dict[str, ToolDeclaration] = field(default_factory=dict)

    @classmethod
    def of(cls, tools: Iterable[ToolDeclaration] = ()) -> "ToolRegistry":
        return cls({tool.name: tool for tool in tools})

    def declarations(self) -> tuple[ToolDeclaration, ...]:
        return tuple(self._tools.values())

    def has(self, name: str) -> bool:
        return name in self._tools
