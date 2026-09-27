"""Build the PydanticAI model from the shared immutable settings.

One factory for all three backends: OpenAI, Gemini's OpenAI-compatible endpoint
and LiteLLM all speak the same /chat/completions wire format, so the provider
switch is only a different base_url and key (settings.resolved_base_url), never
a different SDK. coordinator/model_factory.py does the same for LangChain.
"""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from shared.settings import settings


def get_pydantic_model() -> OpenAIChatModel:
    """The chat model every sub-agent runs on; called once per agent module at import."""
    return OpenAIChatModel(
        settings.LLM_MODEL,
        provider=OpenAIProvider(
            base_url=settings.resolved_base_url,
            api_key=settings.LLM_API_KEY,
        ),
    )
