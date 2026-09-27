"""One wrapping path for every sub-agent: PydanticAI Agent -> A2A app -> self-registered -> served.

Pattern: parameter object. ``AgentSpec`` carries everything that differs per
agent (id, public URL, card text, skills) so the agent modules only declare
data and tools, and the three services cannot drift in how they are exposed.
Nothing here imports another agent's code; agents share this module the same
way they share settings.
"""

from dataclasses import dataclass, field
from urllib.parse import urlparse

import uvicorn
from fasta2a import FastA2A
from fasta2a.pydantic_ai import agent_to_a2a
from fasta2a.schema import Skill
from pydantic_ai import Agent

from agents.registration import RegistryClientFactory, registry_client, with_self_registration

# Inside a container the agent must listen on all interfaces;
BIND_HOST = "0.0.0.0"
TEXT_MODE = "text/plain"
# Appended to every sub-agent's system prompt. The coordinator's LLM has to
# parse these replies and pass the numbers on, so they must stay short, plain
# and exact;
REPLY_STYLE = (
    " Reply in plain text only: no markdown, no LaTeX, no bullet lists. At most "
    "three sentences. State every number from the tool result exactly as returned, and "
    "nothing that is not in the tool result: no extra items, machine types or derived "
    "figures, even if the request mentions them. If the tool reports something as unknown, "
    "say so instead of substituting the closest match. Do not add advice, schedules or "
    "next steps; GAIA and the other subfunctions handle those."
)


@dataclass(frozen=True)
class AgentSpec:
    """Everything that differs between the three agents' A2A cards.

    ``agent_id`` is the registry key and the LiteLLM agent_name; ``public_url``
    is what the card advertises, what the registry stores and (via its port)
    what the process binds to. Frozen because a spec is read at import and
    must not change while the card built from it is already registered.
    """

    agent_id: str
    public_url: str
    description: str
    skills: list[Skill] = field(default_factory=list)


def text_skill(skill_id: str, name: str, description: str, tags: list[str]) -> Skill:
    """A card skill that takes and returns plain text, the only mode our agents use."""
    return Skill(
        id=skill_id,
        name=name,
        description=description,
        tags=tags,
        input_modes=[TEXT_MODE],
        output_modes=[TEXT_MODE],
    )


def build_a2a_app(
    agent: Agent,
    spec: AgentSpec,
    client_factory: RegistryClientFactory = registry_client,
) -> FastA2A:
    """The served card is built from the spec, and the registry receives that same card."""
    app = agent_to_a2a(
        agent,
        name=spec.agent_id,
        url=spec.public_url,
        description=spec.description,
        skills=spec.skills,
    )
    return with_self_registration(app, spec.agent_id, client_factory)


def port_of(public_url: str) -> int:
    """The port to bind, taken from the public URL so one setting drives card, registry and socket."""
    port = urlparse(public_url).port
    if port is None:
        raise ValueError(f"agent url '{public_url}' must include an explicit port")
    return port


def serve(app: FastA2A, spec: AgentSpec) -> None:
    """Run the agent process: uvicorn drives the app's lifespan (worker, then registration) and then serves."""
    uvicorn.run(app, host=BIND_HOST, port=port_of(spec.public_url))
