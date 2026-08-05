ARG IMAGE="python:3.13-slim-bookworm"

FROM ${IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

ENV UV_CACHE_DIR=/root/.cache/uv
RUN --mount=type=cache,id=uv-cache-core,target=${UV_CACHE_DIR} \
    uv sync --frozen --compile-bytecode

FROM ${IMAGE} AS runtime

WORKDIR /app

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8080
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY .. .

CMD ["uvicorn", "challenge.main:app", "--port", "8080", "--host", "0.0.0.0"]