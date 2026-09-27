"""Centralized, immutable configuration shared by every LLM process.

Pattern: single source of truth for configuration. Every process (registry,
each agent, the coordinator) imports the one ``settings`` instance below, so a
backend switch is a single env var flip that changes all of them together.
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# homework_teil2/, two levels above this file. Anchoring .env here (instead of
# the current working directory) makes `python -m spikes...`, PyCharm run
# configurations and pytest all find the same file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

LlmBackend = Literal["openai", "gemini", "litellm"]
RegistryMode = Literal["own", "litellm"]
AgentCallMode = Literal["direct", "litellm"]

# One config, two BaseSettings classes below: both read the same .env, but only
# ``Settings`` is public. frozen=True makes an instance immutable after load.
_SHARED_ENV_CONFIG = SettingsConfigDict(
    env_file=ENV_FILE,
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
    frozen=True,
)


class _BackendEndpoints(BaseSettings):
    """Module-private on purpose. These are the ONLY provider-specific names in
    the codebase and nothing outside this module may read them; callers only
    ever see ``settings.resolved_base_url``. Values can be overridden in .env.
    """

    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    GEMINI_OPENAI_COMPATIBLE_BASE_URL: str = (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    LITELLM_COMPOSE_BASE_URL: str = "http://litellm:4000/v1"

    model_config = _SHARED_ENV_CONFIG

    def default_for(self, backend: LlmBackend) -> str:
        return {
            "openai": self.OPENAI_BASE_URL,
            "gemini": self.GEMINI_OPENAI_COMPATIBLE_BASE_URL,
            "litellm": self.LITELLM_COMPOSE_BASE_URL,
        }[backend]


# Loaded once at import, like ``settings`` below; consulted only by resolved_base_url.
_backend_endpoints = _BackendEndpoints()


class Settings(BaseSettings):
    """The single environment-backed LLM configuration for the whole stack."""

    LLM_BACKEND: LlmBackend = "litellm"
    LLM_API_KEY: str = ""
    # Optional override. Leave empty to use the default for LLM_BACKEND.
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""

    # Where agents self-register and the coordinator discovers them.
    REGISTRY_URL: str = "http://registry:8000"

    # Where the terminal client (coordinator/main.py) finds the running GAIA
    # service (coordinator/api.py). Host-side default; compose publishes 8080.
    COORDINATOR_URL: str = "http://127.0.0.1:8080"

    # Public address of each sub-agent: printed on its agent card, pushed into
    # the registry, and the port it binds to (see agents.a2a_server.port_of).
    # Defaults are the compose service names; local runs override in .env.
    AETHER_AGENT_URL: str = "http://aether-agent:8001"
    DEMETER_AGENT_URL: str = "http://demeter-agent:8002"
    HEPHAESTUS_AGENT_URL: str = "http://hephaestus-agent:8003"

    # own: discover via the hand-rolled registry above. litellm: discover via the
    # LiteLLM proxy's GET /v1/agents instead. Only meaningful if LiteLLM runs.
    REGISTRY_MODE: RegistryMode = "own"
    # direct: the coordinator POSTs to the url in the registry entry. litellm:
    # it POSTs to the proxy's /a2a/{agent_id} relay instead. Same call either way.
    AGENT_CALL_MODE: AgentCallMode = "direct"
    # The LiteLLM proxy as infrastructure (agent registry, A2A relay), separate
    # from LLM_BACKEND=litellm which is about where chat completions go.
    LITELLM_PROXY_URL: str = "http://litellm:4000"
    LITELLM_PROXY_API_KEY: str = ""

    # False starts the coordinator without contacting any agent. Tests use it, and
    # so does anyone running the coordinator alone while the agents are down.
    BRIEFING_ON_STARTUP: bool = True

    model_config = _SHARED_ENV_CONFIG

    # A property, not a field: it is derived from LLM_BACKEND and LLM_BASE_URL and
    # must never be settable from the environment on its own.
    @property
    def resolved_base_url(self) -> str:
        """The base_url every model factory must use.

        An explicit LLM_BASE_URL always wins (needed for a LiteLLM instance that
        is not at the compose default); otherwise the backend's known endpoint.
        """
        if self.LLM_BASE_URL:
            return self.LLM_BASE_URL
        return _backend_endpoints.default_for(self.LLM_BACKEND)


settings = Settings()
