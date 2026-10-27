FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY hackathon_guardrail ./hackathon_guardrail

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "hackathon_guardrail.main:app", "--host", "0.0.0.0", "--port", "8000"]
