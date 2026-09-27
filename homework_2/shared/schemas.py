"""The ONE definition of a registry entry, shared by registry_service/, agents/
and coordinator/. None of them redefines this shape.

Why a flattened record instead of storing the raw A2A card: fasta2a serves
/.well-known/agent-card.json in camelCase with no top-level ``id`` and no
top-level ``url`` (the reachable address is ``supportedInterfaces[0].url``).
The coordinator needs exactly an id to pick, a url to call and some text to
reason over, so ``AgentCard`` carries those, and ``AgentCard.from_a2a_card`` is
the single adapter from the wire card into it.
"""
from typing import Any

from pydantic import BaseModel, Field

A2A_INTERFACES_KEY = "supportedInterfaces"
A2A_INTERFACE_URL_KEY = "url"


class AgentSkill(BaseModel):
    """One capability an agent advertises (subset of the A2A skill object)."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """A registry entry: what the coordinator needs to pick and reach an agent."""

    id: str
    name: str
    description: str = ""
    url: str
    capabilities: list[str] = Field(default_factory=list)
    skills: list[AgentSkill] = Field(default_factory=list)

    @classmethod
    def from_a2a_card(cls, agent_id: str, card: dict[str, Any], url: str | None = None) -> "AgentCard":
        """Flatten a served /.well-known/agent-card.json into a registry entry.

        ``url`` overrides what the card declares. Needed for LiteLLM's stored
        cards, whose supportedInterfaces[0].url is the proxy relay address while
        the real upstream address sits in the top-level ``url``.
        """
        skills = [AgentSkill.model_validate(skill) for skill in card.get("skills", [])]
        return cls(
            id=agent_id,
            name=card["name"],
            description=card.get("description", ""),
            url=url or _first_interface_url(card),
            capabilities=_capabilities_from_skills(skills),
            skills=skills,
        )


class AgentListing(BaseModel):
    """What GET /agents returns, regardless of which registry backend produced it."""

    agents: list[AgentCard] = Field(default_factory=list)


def _first_interface_url(card: dict[str, Any]) -> str:
    """Reachable url from either card style.

    fasta2a (A2A v1 cards) puts it under supportedInterfaces[0].url; LiteLLM
    stores the older style with a top-level url. Accept both, prefer the former.
    """
    interfaces = card.get(A2A_INTERFACES_KEY) or []
    if interfaces:
        return interfaces[0][A2A_INTERFACE_URL_KEY]
    if card.get(A2A_INTERFACE_URL_KEY):
        return card[A2A_INTERFACE_URL_KEY]
    raise ValueError(f"agent card '{card.get('name')}' declares no url")


def _capabilities_from_skills(skills: list[AgentSkill]) -> list[str]:
    """Skill ids plus every tag, de-duplicated, in declaration order."""
    seen: dict[str, None] = {}
    for skill in skills:
        seen.setdefault(skill.id, None)
        for tag in skill.tags:
            seen.setdefault(tag, None)
    return list(seen)
