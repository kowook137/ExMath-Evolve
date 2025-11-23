#!/usr/bin/env bash
echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}"
set -euo pipefail

CONFIG_FILE=${1:-lite_config.yaml}
PORT=${LITELLM_PROXY_PORT:-4000}
EXTRA_ARGS=${LITELLM_PROXY_ARGS:-}

if ! command -v litellm >/dev/null 2>&1; then
  echo "[start_litellm_proxy] litellm is not installed. Run 'pip install litellm' first." >&2
  exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[start_litellm_proxy] Config file '$CONFIG_FILE' not found. Copy lite_config.example.yaml and fill in your keys." >&2
  exit 1
fi

echo "Launching LiteLLM proxy on port $PORT using $CONFIG_FILE"
export OPENAI_API_BASE="http://localhost:${PORT}"
export OPENAI_API_KEY=${OPENAI_API_KEY:-dummy-key}

exec litellm --port "$PORT" --config "$CONFIG_FILE" $EXTRA_ARGS
