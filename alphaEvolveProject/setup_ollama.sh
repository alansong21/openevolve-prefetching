#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-mistral}"           # change to e.g. llama3.2, phi3:mini, qwen2.5-coder, etc.
HOST="${HOST:-127.0.0.1}"           # default bind (keep this unless you REALLY need remote access)
PORT="${PORT:-11434}"

log(){ printf "\n\033[1;32m[ollama]\033[0m %s\n" "$*"; }

install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama already present, updating via install.sh ..."
    curl -fsSL https://ollama.com/install.sh | sh
    return
  fi

  log "Installing Ollama (auto-detects arch) ..."
  if curl -fsSL https://ollama.com/install.sh | sh; then
    return
  fi

  # Fallback: manual tarball by arch
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64)   PKG="ollama-linux-amd64.tgz"  ;;
    aarch64|arm64)  PKG="ollama-linux-arm64.tgz"  ;;
    *) echo "Unsupported arch: $ARCH"; exit 1 ;;
  esac
  log "Install script failed; using manual tarball for $ARCH ..."
  curl -fsSL "https://ollama.com/download/${PKG}" | sudo tar zx -C /usr
}

ensure_service() {
  if command -v systemctl >/dev/null 2>&1; then
    # Make sure service exists & is running
    sudo systemctl enable --now ollama || true

    # Set bind host (default 127.0.0.1). Change HOST=0.0.0.0 when calling this script if you need LAN access.
    sudo systemctl set-environment OLLAMA_HOST="${HOST}:${PORT}"
    sudo systemctl restart ollama
    systemctl --no-pager --full status ollama | sed -n '1,5p' || true
  else
    # Non-systemd environments can run foreground:
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 2
  fi
}

wait_ready() {
  log "Waiting for Ollama API on http://${HOST}:${PORT} ..."
  for _ in $(seq 1 30); do
    if curl -fsS "http://${HOST}:${PORT}/api/tags" >/dev/null; then return; fi
    sleep 1
  done
  echo "Ollama API didn't come up in time"; exit 1
}

pull_and_test() {
  log "Pulling model: ${MODEL}"
  ollama pull "${MODEL}"

  log "Smoke test (/api/generate)"
  curl -fsS "http://${HOST}:${PORT}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"Say hello in one short sentence.\"}" \
    | sed -n '1,5p'
}

main() {
  install_ollama
  ensure_service
  wait_ready
  pull_and_test
  log "Done. Try:  ollama run ${MODEL}"
  echo "API: curl http://${HOST}:${PORT}/api/chat -d '{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'"
}
main "$@"

# --- Set dummy OpenAI API key for Ollama's OpenAI-compatible endpoint ---
export OPENAI_API_KEY="ollama"
echo "[ollama] Exported OPENAI_API_KEY=ollama (for local use)"