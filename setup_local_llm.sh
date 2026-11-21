#!/usr/bin/env bash
# Free, private coach: installs Ollama and pulls a small open model. No API key, nothing leaves your machine.
# Usage: ./setup_local_llm.sh [model]      default model: qwen2.5:3b (~2 GB). Better but slower: qwen2.5:7b (~4.7 GB)
set -euo pipefail
MODEL="${1:-qwen2.5:3b}"
say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

if ! command -v ollama >/dev/null 2>&1; then
  say "Installing Ollama"
  if command -v brew >/dev/null 2>&1; then brew install ollama
  elif [ "$(uname)" = "Linux" ]; then curl -fsSL https://ollama.com/install.sh | sh
  else echo "Install Ollama from https://ollama.com/download then re-run." >&2; exit 1; fi
fi

if ! curl -s -m 2 localhost:11434/api/tags >/dev/null; then
  say "Starting the Ollama server"
  if command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then brew services start ollama >/dev/null
  else nohup ollama serve >/tmp/ollama-serve.log 2>&1 & fi
  for _ in $(seq 1 20); do curl -s -m 1 localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi

say "Pulling $MODEL"
ollama pull "$MODEL"

if ! grep -q '^LOCAL_LLM_MODEL=' .env 2>/dev/null; then
  { echo "LLM_PROVIDER=local"; echo "LOCAL_LLM_MODEL=$MODEL"; } >> .env
  say "Added LLM_PROVIDER=local and LOCAL_LLM_MODEL=$MODEL to .env"
fi
say "Done. Restart Chessy; /api/health should show coach: local"
