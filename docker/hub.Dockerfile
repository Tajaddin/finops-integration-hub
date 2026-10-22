# The hub's API image: locked dependencies from uv.lock, non-root, no build tools in the final stage.
FROM ghcr.io/astral-sh/uv:0.10.4 AS uv

FROM python:3.12.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --extra triage
COPY src ./src
RUN uv sync --locked --extra triage

FROM python:3.12.12-slim
RUN useradd --system --uid 10001 --no-create-home hub
WORKDIR /app
COPY --from=build --chown=hub:hub /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1
USER hub
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "hub.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
