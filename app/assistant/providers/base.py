"""Provider-neutral interface between the booking agent and an LLM.

A provider only translates between its own API format and these small types.
The tool loop, validation and safety checks live in `app.assistant.agent`, so they
are shared by every provider. To add a provider, implement `LLMProvider` and
`Conversation`, then register it in `factory.py`.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any  # normally a dict, but small models sometimes send strings or junk
    from_text: bool = False  # recovered from plain text instead of a native tool call


@dataclass
class ToolResult:
    call: ToolCall
    content: str  # JSON text sent back to the model
    error_code: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error_code is not None


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ChatMessage:
    """A completed user/assistant exchange, used to rebuild a conversation on any provider."""

    role: Literal["user", "assistant"]
    content: str


class ProviderError(Exception):
    """The model couldn't be used. `user_message` is safe to show to the patient."""

    allow_fallback = True

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message


class ProviderUnavailable(ProviderError):
    """Server down, unreachable, timed out, or overloaded."""


class ProviderNotConfigured(ProviderError):
    """Missing API key, unknown model, or local model not pulled."""


class ModelResponseError(ProviderError):
    """The model answered but the answer can't be used (e.g. a refusal or a cut-off reply)."""

    allow_fallback = False


class Conversation(ABC):
    """One chat with the model. Keeps the provider's native message history."""

    @abstractmethod
    def send_user_message(self, text: str) -> ModelTurn: ...

    @abstractmethod
    def send_tool_results(self, results: Sequence[ToolResult]) -> ModelTurn: ...

    @abstractmethod
    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        """Record an exchange that happened without the model (e.g. a confirm button click)."""


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def start_conversation(
        self, system_prompt: str, tools: Sequence[ToolSpec], history: Sequence[ChatMessage] = ()
    ) -> Conversation: ...

    def check_ready(self) -> None:
        """Raise ProviderError if the provider can't be used right now."""

    @property
    def label(self) -> str:
        return f"{self.name} · {self.model}"
