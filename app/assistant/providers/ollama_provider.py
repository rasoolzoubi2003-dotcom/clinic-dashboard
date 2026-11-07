"""Local LLM through Ollama (default provider). Nothing leaves the machine."""

import logging
from collections.abc import Sequence

import httpx
import ollama

from app.assistant.providers.base import (
    ChatMessage,
    Conversation,
    LLMProvider,
    ModelTurn,
    ProviderNotConfigured,
    ProviderUnavailable,
    ToolCall,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str,
        temperature: float = 0.1,
        timeout: float = 120.0,
        client: ollama.Client | None = None,
    ):
        self.model = model
        self.host = host
        self.temperature = temperature
        self.client = client or ollama.Client(host=host, timeout=timeout)

    def start_conversation(
        self, system_prompt: str, tools: Sequence[ToolSpec], history: Sequence[ChatMessage] = ()
    ) -> Conversation:
        return OllamaConversation(self, system_prompt, tools, history)

    def check_ready(self) -> None:
        self._call(self.client.show, self.model)

    # -- internals ---------------------------------------------------------

    def _not_pulled(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"The local model '{self.model}' isn't installed yet. Run: ollama pull {self.model} "
            "(with Docker, it downloads automatically on first start; this can take a few minutes)."
        )

    def _call(self, fn, *args, **kwargs):
        """Run an Ollama client call and turn failures into ProviderErrors."""
        try:
            return fn(*args, **kwargs)
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                raise self._not_pulled() from exc
            raise ProviderUnavailable("The local AI model returned an error. Please try again.", str(exc)) from exc
        except ConnectionError as exc:  # raised by the ollama client when it can't connect
            raise ProviderUnavailable(
                f"Can't reach Ollama at {self.host}. Start it (ollama serve) and make sure the model "
                f"is installed (ollama pull {self.model}).",
                str(exc),
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(
                "The local AI model took too long to respond. Please try again.", str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Can't reach Ollama at {self.host}.", str(exc)) from exc

    def _chat(self, messages: list, tools: list) -> ollama.ChatResponse:
        kwargs = dict(model=self.model, messages=messages, tools=tools, options={"temperature": self.temperature})
        try:
            return self._call(self.client.chat, **kwargs)
        except ProviderUnavailable as exc:
            # Ollama answers 500 when a small model emits a malformed tool call; one retry usually fixes it.
            if "returned an error" not in exc.user_message:
                raise
            logger.warning("Ollama chat failed, retrying once: %s", exc)
            return self._call(self.client.chat, **kwargs)


class OllamaConversation(Conversation):
    def __init__(
        self,
        provider: OllamaProvider,
        system_prompt: str,
        tools: Sequence[ToolSpec],
        history: Sequence[ChatMessage],
    ):
        self._provider = provider
        self._tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]
        self.messages: list = [{"role": "system", "content": system_prompt}]
        self.messages += [{"role": m.role, "content": m.content} for m in history]
        self._call_count = 0

    def send_user_message(self, text: str) -> ModelTurn:
        self.messages.append({"role": "user", "content": text})
        return self._complete()

    def send_tool_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        for result in results:
            self.messages.append({"role": "tool", "content": result.content, "tool_name": result.call.name})
        return self._complete()

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": assistant_text})

    def _complete(self) -> ModelTurn:
        response = self._provider._chat(self.messages, self._tools)
        message = response.message
        self.messages.append(message)

        calls = []
        for tool_call in message.tool_calls or []:
            self._call_count += 1
            calls.append(
                ToolCall(
                    id=f"call_{self._call_count}",
                    name=tool_call.function.name,
                    arguments=dict(tool_call.function.arguments or {}),
                )
            )
        return ModelTurn(text=message.content or "", tool_calls=calls)
