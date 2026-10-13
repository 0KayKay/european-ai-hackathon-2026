from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from hackathon.agent.challenge1 import add_challenge1
from hackathon.agent.dependencies import AgentDependencies
from hackathon.config import get_settings


def build_agent(
    deps: AgentDependencies,
    model_id: str,
    difficulty: str = "EASY",
    mode: str = "CHALLENGE_1",
) -> Agent[AgentDependencies]:
    settings = get_settings()
    model = OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=settings.AGENTGATEWAY_URL,
                api_key=settings.AGENTGATEWAY_API_KEY,
                default_headers={"x-challenge-difficulty": difficulty}
                if mode == "CHALLENGE_1"
                else {},
            ),
        ),
    )
    agent = Agent[AgentDependencies](
        model=model,
        capabilities=[],
        name="hidden-mailbox" if mode == "CHALLENGE_1" else "playground",
    )
    if mode == "CHALLENGE_1":
        add_challenge1(agent, difficulty)
    return agent
