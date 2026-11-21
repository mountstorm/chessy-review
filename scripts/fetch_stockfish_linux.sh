#!/usr/bin/env bash
# Downloads the official Linux x86-64 Stockfish build into vendor/ so it ships with the Vercel function.
# (The macOS/Homebrew binary can't run on Vercel.) vendor/ is gitignored; `vercel deploy` uploads it from disk.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-sf_19}"
mkdir -p vendor && cd vendor
rm -rf _dl && mkdir _dl
echo "Downloading Stockfish $TAG (linux x86-64 universal)…"
curl -fsSL -o _dl/sf.tar.gz "https://github.com/official-stockfish/Stockfish/releases/download/$TAG/stockfish-linux-x86-64-universal.tar.gz"
tar -xzf _dl/sf.tar.gz -C _dl
BIN="$(find _dl -type f -name 'stockfish*' ! -name '*.tar.gz' ! -name '*.md' ! -name '*.txt' | head -1)"
[ -n "$BIN" ] || { echo "binary not found in archive" >&2; exit 1; }
mv "$BIN" stockfish-linux-x86-64 && chmod 755 stockfish-linux-x86-64
rm -rf _dl
file stockfish-linux-x86-64 2>/dev/null || true
du -h stockfish-linux-x86-64
