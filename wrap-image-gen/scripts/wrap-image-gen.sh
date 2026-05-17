#!/usr/bin/env sh
set -eu

# Fill these in manually before running real API calls.
# Keep real credentials local and do not commit or share this file after editing.
export OPENAI_API_KEY=""
export OPENAI_BASE_URL=""

if [ -z "${CODEX_HOME:-}" ]; then
  if [ -z "${HOME:-}" ]; then
    echo "Error: CODEX_HOME is unset and HOME is unavailable." >&2
    exit 1
  fi
  CODEX_HOME="$HOME/.codex"
fi
export CODEX_HOME

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
IMAGE_GEN="${IMAGEGEN_CLI:-$CODEX_HOME/skills/.system/imagegen/scripts/image_gen.py}"

if [ ! -f "$IMAGE_GEN" ]; then
  echo "Error: bundled imagegen CLI not found at $IMAGE_GEN" >&2
  exit 1
fi

NEEDS_KEY=1
for ARG in "$@"; do
  if [ "$ARG" = "--dry-run" ]; then
    NEEDS_KEY=0
  fi
done

if [ "$NEEDS_KEY" -eq 1 ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "Error: OPENAI_API_KEY is required for real API calls." >&2
  echo "Fill in OPENAI_API_KEY at the top of this wrapper before running real API calls." >&2
  exit 1
fi

UV_CMD="${UV_BIN:-$(command -v uv 2>/dev/null || true)}"
if [ -z "$UV_CMD" ] || ! "$UV_CMD" --version >/dev/null 2>&1; then
  echo "Error: uv is required to run the wrapped imagegen CLI with dependencies." >&2
  echo "Install uv or set UV_BIN to the uv executable path." >&2
  exit 1
fi

exec "$UV_CMD" run --with openai --with pillow python "$IMAGE_GEN" "$@"
