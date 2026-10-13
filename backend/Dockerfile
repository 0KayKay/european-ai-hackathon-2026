FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY hackathon ./hackathon

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "hackathon.main:app", "--host", "0.0.0.0", "--port", "8000"]
