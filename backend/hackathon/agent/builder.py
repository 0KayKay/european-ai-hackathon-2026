from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from hackathon.agent.challenge_1.agent import add_challenge1
from hackathon.agent.challenge_2.agent import add_challenge2
from hackathon.agent.challenge_3.agent import add_challenge3
from hackathon.agent.dependencies import AgentDependencies
from hackathon.agent.guardrails import ChallengeGuardrails
from hackathon.config import get_settings


def build_agent(
    deps: AgentDependencies,
    model_id: str,
    difficulty: str = "EASY",
    mode: str = "CHALLENGE_1",
) -> Agent[AgentDependencies]:
    settings = get_settings()
    from pydantic_ai import ModelSettings

    model = OpenAIChatModel(
        model_id,
        settings=ModelSettings(
            thinking=False,
        ),
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=settings.AGENTGATEWAY_URL,
                api_key=settings.AGENTGATEWAY_API_KEY,
                default_headers={},
            ),
        ),
    )
    agent = Agent[AgentDependencies](
        model=model,
        capabilities=[ChallengeGuardrails()] if mode != "PLAYGROUND" else [],
        name={
            "CHALLENGE_1": "hidden-mailbox",
            "CHALLENGE_2": "email-review",
            "CHALLENGE_3": "offer-review",
        }.get(mode, "playground"),
    )
    if mode == "CHALLENGE_1":
        add_challenge1(agent, difficulty)
    elif mode == "CHALLENGE_2":
        add_challenge2(agent, difficulty)
    elif mode == "CHALLENGE_3":
        add_challenge3(agent, difficulty)
    else:

        @agent.instructions
        def identify_user(ctx: RunContext[AgentDependencies]) -> str:
            return (
                f"The authenticated user's name is {ctx.deps.get('user_name', 'User')}."
            )

    return agent
