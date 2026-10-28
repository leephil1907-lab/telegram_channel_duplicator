#!/usr/bin/env bash
# One-shot reproducible build: venv -> deps -> tests -> sdist/wheel -> verify.
# Usage: ./build.sh   (or PYTHON=python3.11 ./build.sh)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
PYBIN="$VENV/bin/python"

echo "==> [1/5] Creating virtual environment (.venv)"
"$PYTHON" -m venv "$VENV"
"$PYBIN" -m pip install --quiet --upgrade pip

echo "==> [2/5] Installing runtime + dev dependencies"
"$PYBIN" -m pip install --quiet -e ".[dev]"
"$PYBIN" -m pip check
"$PYBIN" -m compileall -q telegram_channel_duplicator

echo "==> [3/5] Running offline test suite (unit + multi-user HTTP API)"
"$PYBIN" -m pytest

echo "==> [4/5] Building sdist + wheel into artifacts/"
mkdir -p "$ROOT/artifacts"
rm -f "$ROOT"/artifacts/*.whl "$ROOT"/artifacts/*.tar.gz
"$PYBIN" -m build --outdir "$ROOT/artifacts"

echo "==> [5/5] Verifying the built wheel in a clean temporary target"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
WHEEL="$(ls "$ROOT"/artifacts/*.whl | head -n1)"
"$PYBIN" -m pip install --quiet --no-deps --target "$TMP_DIR/site" "$WHEEL"

# Ephemeral verification fixture only — not shipped with the project.
cat > "$TMP_DIR/config.yaml" <<'YAML'
account_phone: "+15551234567"
api_id: 12345678
api_hash: "0123456789abcdef0123456789abcdef"
delay: 5
edit_message_checker_limit: 10
groups:
  - name: verify
    sources: ["A"]
    destinations: ["B"]
    whitelist: []
YAML
(
  cd "$TMP_DIR"
  export PYTHONPATH="$TMP_DIR/site"
  "$PYBIN" -c "import telegram_channel_duplicator as p; print('imported', p.__version__)"
  "$PYBIN" -m telegram_channel_duplicator --version
  "$PYBIN" -m telegram_channel_duplicator --check
  "$PYBIN" -c "from telegram_channel_duplicator import auth, workspaces, dashboard, runner, store, timing; print('production modules OK')"
)

echo "==> BUILD OK"
ls -lh "$ROOT"/artifacts/*.whl "$ROOT"/artifacts/*.tar.gz
