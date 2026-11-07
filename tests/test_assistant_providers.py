"""Provider adapters, tested with mocked SDK clients (no network)."""

from unittest.mock import MagicMock

import anthropic
import httpx
import httpx2
import ollama
import pytest
from anthropic.types import Message

from app.assistant.providers.anthropic_provider import AnthropicProvider
from app.assistant.providers.base import (
    ChatMessage,
    ModelResponseError,
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    ToolCall,
    ToolResult,
)
from app.assistant.providers.factory import build_provider, build_providers
from app.assistant.providers.ollama_provider import OllamaProvider
from app.assistant.tools import TOOL_SPECS

HISTORY = [ChatMessage("user", "hi"), ChatMessage("assistant", "Hello!")]


def record_calls(mock_method, *replies):
    """Return the given replies in order, saving a copy of the messages sent on each call.

    The conversation keeps appending to the same list after each call, so the mock's own
    call_args would show the list as it is now, not as it was sent.
    """
    sent: list[list] = []
    queue = list(replies)

    def side_effect(*args, **kwargs):
        sent.append(list(kwargs["messages"]))
        return queue.pop(0)

    mock_method.side_effect = side_effect
    return sent


# --- Ollama ------------------------------------------------------------------


def ollama_reply(content: str = "", tool_calls: list[tuple[str, dict]] = ()) -> ollama.ChatResponse:
    calls = [
        ollama.Message.ToolCall(function=ollama.Message.ToolCall.Function(name=name, arguments=args))
        for name, args in tool_calls
    ]
    return ollama.ChatResponse(
        model="qwen2.5:7b", message=ollama.Message(role="assistant", content=content, tool_calls=calls or None)
    )


@pytest.fixture
def ollama_client():
    return MagicMock(spec=ollama.Client)


@pytest.fixture
def ollama_provider(ollama_client):
    return OllamaProvider(model="qwen2.5:7b", host="http://localhost:11434", temperature=0.1, client=ollama_client)


def test_ollama_request_and_tool_calls(ollama_provider, ollama_client):
    ollama_client.chat.return_value = ollama_reply(tool_calls=[("find_available_slots", {"doctor": "skin"})])
    conversation = ollama_provider.start_conversation("SYSTEM", TOOL_SPECS, HISTORY)

    turn = conversation.send_user_message("skin doctor please")

    kwargs = ollama_client.chat.call_args.kwargs
    assert kwargs["model"] == "qwen2.5:7b"
    assert kwargs["options"] == {"temperature": 0.1}
    assert [m["role"] for m in kwargs["messages"][:4]] == ["system", "user", "assistant", "user"]
    assert kwargs["tools"][1]["function"]["name"] == "find_available_slots"
    assert turn.tool_calls[0].name == "find_available_slots"
    assert turn.tool_calls[0].arguments == {"doctor": "skin"}


def test_ollama_tool_results_are_sent_as_tool_messages(ollama_provider, ollama_client):
    sent = record_calls(ollama_client.chat, ollama_reply(tool_calls=[("list_doctors", {})]), ollama_reply("Four doctors."))
    conversation = ollama_provider.start_conversation("SYSTEM", TOOL_SPECS)
    turn = conversation.send_user_message("doctors?")

    final = conversation.send_tool_results([ToolResult(call=turn.tool_calls[0], content='{"doctors": []}')])

    assert final.text == "Four doctors."
    last_message = sent[-1][-1]
    assert last_message == {"role": "tool", "content": '{"doctors": []}', "tool_name": "list_doctors"}


def test_ollama_model_not_pulled(ollama_provider, ollama_client):
    ollama_client.chat.side_effect = ollama.ResponseError("model 'qwen2.5:7b' not found", 404)
    with pytest.raises(ProviderNotConfigured) as exc_info:
        ollama_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("hi")
    assert "ollama pull qwen2.5:7b" in exc_info.value.user_message


def test_ollama_not_running(ollama_provider, ollama_client):
    ollama_client.chat.side_effect = ConnectionError("Failed to connect to Ollama")
    with pytest.raises(ProviderUnavailable) as exc_info:
        ollama_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("hi")
    assert "Can't reach Ollama at http://localhost:11434" in exc_info.value.user_message
    assert "ollama pull qwen2.5:7b" in exc_info.value.user_message


def test_ollama_timeout(ollama_provider, ollama_client):
    ollama_client.chat.side_effect = httpx.ReadTimeout("timed out")
    with pytest.raises(ProviderUnavailable) as exc_info:
        ollama_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("hi")
    assert "took too long" in exc_info.value.user_message


def test_ollama_server_error_is_retried_once(ollama_provider, ollama_client):
    ollama_client.chat.side_effect = [ollama.ResponseError("error parsing tool call", 500), ollama_reply("Hi!")]
    turn = ollama_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("hi")
    assert turn.text == "Hi!"
    assert ollama_client.chat.call_count == 2


def test_ollama_check_ready(ollama_provider, ollama_client):
    ollama_provider.check_ready()
    ollama_client.show.assert_called_once_with("qwen2.5:7b")

    ollama_client.show.side_effect = ollama.ResponseError("not found", 404)
    with pytest.raises(ProviderNotConfigured):
        ollama_provider.check_ready()


# --- Anthropic ---------------------------------------------------------------


def claude_reply(content: list[dict], stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


@pytest.fixture
def claude_client():
    return MagicMock()


@pytest.fixture
def claude_provider(claude_client):
    return AnthropicProvider(model="claude-haiku-5-5", client=claude_client)


def test_anthropic_requires_an_api_key():
    with pytest.raises(ProviderNotConfigured, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(model="claude-haiku-5-5", api_key="")


def test_anthropic_request_and_tool_calls(claude_provider, claude_client):
    sent = record_calls(
        claude_client.messages.create,
        claude_reply(
            [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "list_doctors", "input": {}},
            ],
            stop_reason="tool_use",
        ),
    )
    conversation = claude_provider.start_conversation("SYSTEM", TOOL_SPECS, HISTORY)

    turn = conversation.send_user_message("doctors?")

    kwargs = claude_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["system"] == "SYSTEM"
    assert kwargs["tools"][0] == {
        "name": "list_doctors",
        "description": TOOL_SPECS[0].description,
        "input_schema": TOOL_SPECS[0].parameters,
    }
    assert kwargs["output_config"] == {"effort": "low"}
    assert "fallbacks" not in kwargs and "temperature" not in kwargs
    assert [m["role"] for m in sent[0]] == ["user", "assistant", "user"]
    assert turn.text == "Let me check."
    assert turn.tool_calls == [ToolCall(id="toolu_1", name="list_doctors", arguments={})]


def test_anthropic_tool_results_use_tool_use_ids(claude_provider, claude_client):
    sent = record_calls(
        claude_client.messages.create,
        claude_reply([{"type": "tool_use", "id": "toolu_9", "name": "list_doctors", "input": {}}], "tool_use"),
        claude_reply([{"type": "text", "text": "Four doctors."}]),
    )
    conversation = claude_provider.start_conversation("SYSTEM", TOOL_SPECS)
    turn = conversation.send_user_message("doctors?")

    final = conversation.send_tool_results(
        [ToolResult(call=turn.tool_calls[0], content='{"error": "x"}', error_code="invalid_arguments")]
    )

    assert final.text == "Four doctors."
    messages = sent[-1]
    assert messages[1]["content"][0].type == "tool_use"  # assistant content passed back unchanged
    assert messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_9", "content": '{"error": "x"}', "is_error": True}],
    }


def test_anthropic_refusal(claude_provider, claude_client):
    claude_client.messages.create.return_value = claude_reply([], stop_reason="refusal")
    with pytest.raises(ModelResponseError) as exc_info:
        claude_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("something odd")
    assert exc_info.value.allow_fallback is False


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, code: int):
    return cls("error", response=httpx2.Response(code, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected", "text"),
    [
        (anthropic.APIConnectionError(request=_REQUEST), ProviderUnavailable, "internet connection"),
        (anthropic.APITimeoutError(request=_REQUEST), ProviderUnavailable, "internet connection"),
        (_status_error(anthropic.AuthenticationError, 401), ProviderNotConfigured, "API key was rejected"),
        (_status_error(anthropic.NotFoundError, 404), ProviderNotConfigured, "ANTHROPIC_MODEL"),
        (_status_error(anthropic.RateLimitError, 429), ProviderUnavailable, "busy"),
        (_status_error(anthropic.InternalServerError, 529), ProviderUnavailable, "temporarily unavailable"),
        (_status_error(anthropic.BadRequestError, 400), ProviderError, "couldn't process"),
    ],
)
def test_anthropic_errors_are_mapped(claude_provider, claude_client, error, expected, text):
    claude_client.messages.create.side_effect = error
    with pytest.raises(expected) as exc_info:
        claude_provider.start_conversation("SYSTEM", TOOL_SPECS).send_user_message("hi")
    assert text in exc_info.value.user_message


# --- Factory -----------------------------------------------------------------


def test_default_provider_is_local_ollama():
    provider = build_provider("ollama")
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen2.5:7b"
    assert 0 <= provider.temperature <= 0.2


def test_unknown_provider():
    with pytest.raises(ProviderNotConfigured, match="Unknown LLM provider"):
        build_provider("gpt")


def test_claude_without_key_falls_back_to_ollama(monkeypatch):
    monkeypatch.setattr("app.config.ANTHROPIC_API_KEY", "")
    primary, fallback, error = build_providers("anthropic", "ollama")
    assert isinstance(primary, OllamaProvider) and fallback is None
    assert "ANTHROPIC_API_KEY" in error


def test_fallback_is_off_by_default():
    primary, fallback, error = build_providers("ollama", "")
    assert isinstance(primary, OllamaProvider) and fallback is None and error is None
