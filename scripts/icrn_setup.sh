#!/usr/bin/env bash
# Set up ctxdrift on a GPU notebook server without root (iCRN, JupyterHub, HPC):
# Python deps, Node (for the repo's tests), Ollama in ~/.local, and the models.
#   bash scripts/icrn_setup.sh                 # default model qwen2.5:7b
#   MODELS="qwen2.5:7b qwen3:4b" bash scripts/icrn_setup.sh
set -euo pipefail
MODELS="${MODELS:-qwen2.5:7b}"
PREFIX="$HOME/.local"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/ollama-models}"   # point at scratch space if $HOME is small
mkdir -p "$PREFIX/bin" "$OLLAMA_MODELS"
export PATH="$PREFIX/bin:$PREFIX/node/bin:$PATH"

echo "== GPU"; nvidia-smi --query-gpu=name,memory.total --format=csv || echo "No GPU visible: pick a GPU server"

echo "== Python packages"
python3 -m pip install -q --user -r requirements.txt

echo "== Node"
if ! command -v node >/dev/null; then
  V=v22.11.0
  curl -fsSL "https://nodejs.org/dist/$V/node-$V-linux-x64.tar.xz" | tar -xJ -C "$PREFIX"
  rm -rf "$PREFIX/node" && mv "$PREFIX/node-$V-linux-x64" "$PREFIX/node"
fi
node --version

echo "== Ollama"
if ! command -v ollama >/dev/null; then
  mkdir -p "$PREFIX/ollama"
  if curl -fsSL -o /tmp/ollama.tar.zst https://ollama.com/download/ollama-linux-amd64.tar.zst; then
    if command -v zstd >/dev/null; then
      zstd -dc /tmp/ollama.tar.zst | tar -x -C "$PREFIX/ollama"
    else
      python3 -m pip install -q --user zstandard
      python3 -c "import zstandard,sys; zstandard.ZstdDecompressor().copy_stream(open('/tmp/ollama.tar.zst','rb'), open('/tmp/ollama.tar','wb'))"
      tar -xf /tmp/ollama.tar -C "$PREFIX/ollama"
    fi
  else
    curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz | tar -xz -C "$PREFIX/ollama"
  fi
  ln -sf "$PREFIX/ollama/bin/ollama" "$PREFIX/bin/ollama"
  rm -f /tmp/ollama.tar.zst /tmp/ollama.tar
fi

if ! curl -s localhost:11434 >/dev/null; then
  OLLAMA_NUM_PARALLEL=2 OLLAMA_KEEP_ALIVE=-1 nohup ollama serve > "$HOME/ollama.log" 2>&1 &
  for _ in $(seq 60); do curl -s localhost:11434 >/dev/null && break; sleep 1; done
fi
curl -s localhost:11434 && echo
for m in $MODELS; do ollama pull "$m"; done

echo "== Offline tests"
python3 tests/test_smoke.py | grep -cE "^PASS" | xargs -I{} echo "{} of 13 tests passed"
echo
echo "Done. In every new terminal run:  export PATH=\"$PREFIX/bin:$PREFIX/node/bin:\$PATH\" OLLAMA_MODELS=$OLLAMA_MODELS"
