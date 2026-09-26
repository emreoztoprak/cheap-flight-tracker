#!/usr/bin/env sh
# Run a command in a throwaway Python 3.12 + uv container with the repo mounted at /app.
# The virtualenv and uv cache live in .dev-cache/ so repeated runs are fast.
# Usage: ./scripts/dev.sh uv run pytest
set -eu
cd "$(dirname "$0")/.."
mkdir -p .dev-cache
exec docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  -v "$PWD":/app -w /app \
  -v "$PWD/.dev-cache":/cache \
  -e HOME=/cache/home \
  -e UV_CACHE_DIR=/cache/uv \
  -e UV_PROJECT_ENVIRONMENT=/cache/venv \
  -e UV_LINK_MODE=copy \
  -e UV_PYTHON_DOWNLOADS=never \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim "$@"
