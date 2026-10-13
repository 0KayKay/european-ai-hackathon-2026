FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY hackathon_deepteam ./hackathon_deepteam

ENV DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPTEAM_TELEMETRY_OPT_OUT=YES DEEPTEAM_SHOW_PROGRESS=false
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "hackathon_deepteam.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
