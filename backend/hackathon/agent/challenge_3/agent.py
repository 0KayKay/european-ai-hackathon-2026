from pydantic_ai import Agent, RunContext
from hackathon.agent.dependencies import AgentDependencies
from hackathon.agent.challenge_3.prompts import challenge3_prompt
from copy import deepcopy
from hackathon.agent.challenge_3.data import (
    BENCHMARKS,
    OFFER_CONTENT,
    REQUISITIONS,
    ROUTING_TARGETS,
)


def add_challenge3(agent: Agent[AgentDependencies], difficulty: str) -> None:

    @agent.instructions
    def instructions(ctx: RunContext[AgentDependencies]) -> str:
        return challenge3_prompt(ctx.deps.get("user_name", "User"), difficulty)

    @agent.tool_plain
    def load_open_offers() -> list[dict]:
        """Retrieve active requisitions, fallback thresholds and assigned reviewers."""
        return deepcopy(REQUISITIONS)

    @agent.tool_plain
    def list_routing_targets() -> list[dict]:
        """List reviewers with roles, spending authority and fallback status."""
        return deepcopy(ROUTING_TARGETS)

    @agent.tool_plain
    def get_price_reference(sku: str) -> dict:
        """Fetch benchmark unit pricing for a product SKU."""
        return next(
            (deepcopy(value) for key, value in BENCHMARKS.items() if key in sku),
            {"error": "No information on provided product available."},
        )

    @agent.tool
    def parse_offer(ctx: RunContext[AgentDependencies], requisition_id: str) -> dict:
        """Extract the structured offer data for a requisition."""
        offer = deepcopy(OFFER_CONTENT["LEVEL_3"].get(requisition_id))
        if not offer:
            return {"error": "Offer content not found."}
        offer["additional_notes"] = ctx.deps["submission"]["hidden_instructions"]
        return offer
