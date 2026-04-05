FROM python:3.14-slim

WORKDIR /build/mimir-agent

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy SDK so ../norns-sdk-python resolves to /build/norns-sdk-python
COPY norns-sdk-python/ /build/norns-sdk-python/

# Copy project files
COPY norns-mimir-agent/pyproject.toml norns-mimir-agent/uv.lock norns-mimir-agent/README.md ./
COPY norns-mimir-agent/mimir_agent/ ./mimir_agent/

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "mimir-agent"]
