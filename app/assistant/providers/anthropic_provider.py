"""Optional Claude provider (Anthropic API). Used only when LLM_PROVIDER=anthropic and an API key is set."""

from collections.abc import Sequence

import anthropic

from app.assistant.providers.base import (
    ChatMessage,
    Conversation,
    LLMProvider,
    ModelResponseError,
    ModelTurn,
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    ToolCall,
    ToolResult,
    ToolSpec,
)

MAX_TOKENS = 16000


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str = "",
        client: anthropic.Anthropic | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ):
        self.model = model
        if client is None:
            if not api_key:
                raise ProviderNotConfigured(
                    "The Claude provider needs ANTHROPIC_API_KEY in .env. "
                    "Add a key, or set LLM_PROVIDER=ollama to use the local model."
                )
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.client = client

    def start_conversation(
        self, system_prompt: str, tools: Sequence[ToolSpec], history: Sequence[ChatMessage] = ()
    ) -> Conversation:
        return AnthropicConversation(self, system_prompt, tools, history)

    def _create(self, system: str, tools: list, messages: list) -> anthropic.types.Message:
        try:
            return self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": "low"},  # short chat turns don't need deep reasoning
                cache_control={"type": "ephemeral"},  # caches the stable prompt + tools prefix
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderNotConfigured(
                "The Claude API key was rejected. Check ANTHROPIC_API_KEY in .env.", str(exc)
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ProviderNotConfigured(
                f"The Claude model '{self.model}' wasn't found. Check ANTHROPIC_MODEL in .env.", str(exc)
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderUnavailable("Claude is busy right now. Please try again in a moment.", str(exc)) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError("The assistant couldn't process that request.", str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderUnavailable(
                "Claude is temporarily unavailable. Please try again shortly.", str(exc)
            ) from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise ProviderUnavailable(
                "Can't reach the Claude API. Check the internet connection.", str(exc)
            ) from exc


class AnthropicConversation(Conversation):
    def __init__(
        self,
        provider: AnthropicProvider,
        system_prompt: str,
        tools: Sequence[ToolSpec],
        history: Sequence[ChatMessage],
    ):
        self._provider = provider
        self._system = system_prompt
        self._tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        self.messages: list = [{"role": m.role, "content": m.content} for m in history]

    def send_user_message(self, text: str) -> ModelTurn:
        self.messages.append({"role": "user", "content": text})
        return self._complete()

    def send_tool_results(self, results: Sequence[ToolResult]) -> ModelTurn:
        blocks = []
        for result in results:
            if result.call.from_text:  # no tool_use block to answer, so send it as text
                blocks.append({"type": "text", "text": f"Result of {result.call.name}: {result.content}"})
            else:
                block = {"type": "tool_result", "tool_use_id": result.call.id, "content": result.content}
                if result.is_error:
                    block["is_error"] = True
                blocks.append(block)
        # All results go back in one user message.
        self.messages.append({"role": "user", "content": blocks})
        return self._complete()

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": assistant_text})

    def _complete(self) -> ModelTurn:
        response = self._provider._create(self._system, self._tools, self.messages)
        # Append the full content (including any thinking blocks) unchanged, as the API requires.
        self.messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            raise ModelResponseError("Sorry, I can't help with that request. I can only help with appointments.")
        if response.stop_reason == "max_tokens":
            raise ModelResponseError("Sorry, my reply was cut off. Please try again.")

        text = "".join(block.text for block in response.content if block.type == "text")
        calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return ModelTurn(text=text, tool_calls=calls)
