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
