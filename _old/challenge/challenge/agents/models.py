from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_vertexai import ChatVertexAI

from challenge.config import get_settings

settings = get_settings()


@lru_cache
def get_gemini_chat_model(model: str = "gemini-2.5-flash", thinking_budget: int = 0):
    """
    Returns the configured chat model for LangGraph.
    """
    if settings.GEMINI_API_KEY:
        model = ChatGoogleGenerativeAI(
            model=model,
            max_retries=2,
            google_api_key=settings.GEMINI_API_KEY,
            thinking_budget=thinking_budget,
            include_thoughts=thinking_budget != 0,
        )
    else:
        model = ChatVertexAI(
            model=model,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.VERTEXAI_LOCATION,
            max_retries=2,
            thinking_budget=thinking_budget,
            include_thoughts=thinking_budget != 0,
        )

    return model
