"""A scripted LLM provider for tests: returns pre-written turns instead of calling a model."""

from collections.abc import Callable, Sequence

from app.assistant.providers.base import (
    ChatMessage,
    Conversation,
    LLMProvider,
    ModelTurn,
    ToolCall,
    ToolResult,
    ToolSpec,
)


def say(text: str) -> ModelTurn:
    return ModelTurn(text=text)


def call(name: str, **arguments) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=arguments)])


class ScriptedConversation(Conversation):
    def __init__(self, provider: "ScriptedProvider", system_prompt: str, history: Sequence[ChatMessage]):
        self.provider = provider
        self.system_prompt = system_prompt
        self.history = list(history)
        self.user_messages: list[str] = []
        self.tool_results: list[ToolResult] = []
        self.exchanges: list[tuple[str, str]] = []

    def send_user_message(self, text: str) -> ModelTurn:
        self.user_messages.append(text)
        return self.provider.next_turn()

    def send_tool_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        self.tool_results.extend(results)
        return self.provider.next_turn()

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.exchanges.append((user_text, assistant_text))


class ScriptedProvider(LLMProvider):
    """Plays back `turns` in order. A turn can be a ModelTurn, an exception to raise, or a callable."""

    def __init__(self, *turns: ModelTurn | Exception | Callable[[], ModelTurn], name: str = "fake"):
        self.name = name
        self.model = "scripted"
        self.turns = list(turns)
        self.conversations: list[ScriptedConversation] = []
        self.ready_error: Exception | None = None

    def start_conversation(
        self, system_prompt: str, tools: Sequence[ToolSpec], history: Sequence[ChatMessage] = ()
    ) -> Conversation:
        conversation = ScriptedConversation(self, system_prompt, history)
        self.conversations.append(conversation)
        return conversation

    def check_ready(self) -> None:
        if self.ready_error:
            raise self.ready_error

    def next_turn(self) -> ModelTurn:
        if not self.turns:
            raise AssertionError("The scripted provider ran out of turns")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn() if callable(turn) else turn

    @property
    def conversation(self) -> ScriptedConversation:
        return self.conversations[-1]
