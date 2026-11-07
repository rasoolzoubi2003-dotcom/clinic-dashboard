"""Build providers from configuration."""

from app import config
from app.assistant.providers.base import LLMProvider, ProviderNotConfigured

SUPPORTED_PROVIDERS = ("ollama", "anthropic")


def build_provider(name: str) -> LLMProvider:
    """Create the named provider. Raises ProviderNotConfigured if it can't be set up."""
    if name == "ollama":
        from app.assistant.providers.ollama_provider import OllamaProvider

        return OllamaProvider(
            model=config.OLLAMA_MODEL,
            host=config.OLLAMA_HOST,
            temperature=config.OLLAMA_TEMPERATURE,
            timeout=config.OLLAMA_TIMEOUT,
        )
    if name == "anthropic":
        # Imported lazily: the Anthropic SDK is only needed when Claude is selected.
        from app.assistant.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=config.ANTHROPIC_MODEL, api_key=config.ANTHROPIC_API_KEY)
    raise ProviderNotConfigured(
        f"Unknown LLM provider '{name}'. Set LLM_PROVIDER to one of: {', '.join(SUPPORTED_PROVIDERS)}."
    )


def build_providers(primary_name: str, fallback_name: str = "") -> tuple[LLMProvider | None, LLMProvider | None, str | None]:
    """Return (primary, fallback, setup_error) from provider names.

    If the primary can't be set up (e.g. Claude without an API key) but a fallback can,
    the fallback is promoted so the assistant still works.
    """
    errors: list[str] = []
    primary = fallback = None
    try:
        primary = build_provider(primary_name)
    except ProviderNotConfigured as exc:
        errors.append(exc.user_message)
    if fallback_name and fallback_name != primary_name:
        try:
            fallback = build_provider(fallback_name)
        except ProviderNotConfigured as exc:
            errors.append(exc.user_message)
    if primary is None:
        primary, fallback = fallback, None
    return primary, fallback, " ".join(errors) or None
