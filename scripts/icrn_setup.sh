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

echo "== Node (optional: only for the final node --test check)"
# Home on these servers is often a network filesystem where tar fails ("Directory renamed before its status
# could be extracted"), so unpack on local disk first and copy the result home.
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ctxdrift.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
if ! command -v node >/dev/null; then
  V=v22.11.0
  if curl -fsSL -o "$TMP/node.tar.xz" "https://nodejs.org/dist/$V/node-$V-linux-x64.tar.xz" \
     && tar -xJf "$TMP/node.tar.xz" -C "$TMP"; then
    rm -rf "$PREFIX/node" && cp -r "$TMP/node-$V-linux-x64" "$PREFIX/node"
  else
    echo "Node install failed: continuing (tests_pass will show '-')."
  fi
fi
node --version || true

echo "== Ollama"
if ! command -v ollama >/dev/null; then
  mkdir -p "$TMP/ollama"
  if curl -fsSL -o "$TMP/ollama.tar.zst" https://ollama.com/download/ollama-linux-amd64.tar.zst; then
    if command -v zstd >/dev/null; then
      zstd -dc "$TMP/ollama.tar.zst" > "$TMP/ollama.tar"
    else
      python3 -m pip install -q --user zstandard
      python3 -c "import zstandard,sys; zstandard.ZstdDecompressor().copy_stream(open(sys.argv[1],'rb'), open(sys.argv[2],'wb'))" \
        "$TMP/ollama.tar.zst" "$TMP/ollama.tar"
    fi
    tar -xf "$TMP/ollama.tar" -C "$TMP/ollama"
  else
    curl -fsSL -o "$TMP/ollama.tgz" https://ollama.com/download/ollama-linux-amd64.tgz
    tar -xzf "$TMP/ollama.tgz" -C "$TMP/ollama"
  fi
  rm -rf "$PREFIX/ollama" && cp -r "$TMP/ollama" "$PREFIX/ollama"
  ln -sf "$PREFIX/ollama/bin/ollama" "$PREFIX/bin/ollama"
fi
ollama --version || true

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
