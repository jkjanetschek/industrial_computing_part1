"""Build the coordinator model from the shared immutable settings.

Mirror of agents/model_factory.py for LangChain: the same OpenAI-compatible
adapter for every backend, with only base_url, key and model name swapped.
"""

from langchain_openai import ChatOpenAI

from shared.settings import settings


def get_langchain_model() -> ChatOpenAI:
    """The chat model GAIA reasons with; build_coordinator calls this unless a test injects a fake."""
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        base_url=settings.resolved_base_url,
    )
