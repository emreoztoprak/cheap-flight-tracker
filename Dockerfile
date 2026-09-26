FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app && mkdir -p /data /config
COPY --from=build /app/.venv /app/.venv
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CFR_UI_HOST=0.0.0.0
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s \
  CMD ["python", "-c", "import os, sys, time; p = '/data/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 600 else 1)"]
ENTRYPOINT ["docker-entrypoint.sh"]
