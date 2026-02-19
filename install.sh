#!/bin/bash
set -e

# HarvestMCP Installer
# Idempotent — kör om utan problem. Hoppar över steg som redan är klara.
# Laddar ner en fristående Python — inga systemkrav förutom curl och tar.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_DIR="$SCRIPT_DIR/.python"
PYTHON_BIN="$PYTHON_DIR/python/bin/python3"
VENV_DIR="$SCRIPT_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
HARVEST_TOKEN="$HOME/.harvest/token.json"
FORECAST_TOKEN="$HOME/.harvest/forecast_token.json"
CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

# Python-build-standalone version
PYTHON_VERSION="3.12.12"
PYTHON_BUILD_TAG="20260127"

# --- Formattering ---
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
RED=$'\033[0;31m'
BOLD=$'\033[1m'
NC=$'\033[0m'

step() { printf "%s[%s/6]%s %-28s" "$BOLD" "$1" "$NC" "$2"; }
ok()   { printf "%s%s%s\n" "$GREEN" "$1" "$NC"; }
skip() { printf "%sredan klar ✓%s\n" "$YELLOW" "$NC"; }
fail() { printf "%sFEL: %s%s\n" "$RED" "$1" "$NC"; exit 1; }

echo ""
echo "${BOLD}══════════════════════════════════════${NC}"
echo "${BOLD}  HarvestMCP Installer${NC}"
echo "${BOLD}══════════════════════════════════════${NC}"
echo "  $SCRIPT_DIR"
echo ""

# ──────────────────────────────────────
# [1/6] Standalone Python
# ──────────────────────────────────────
step 1 "Python $PYTHON_VERSION"

if [ -x "$PYTHON_BIN" ]; then
    skip
else
    # Detektera arkitektur
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        PYTHON_ARCH="aarch64"
    else
        PYTHON_ARCH="x86_64"
    fi

    FILENAME="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${PYTHON_ARCH}-apple-darwin-install_only.tar.gz"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${FILENAME}"

    echo ""
    printf "   Laddar ner Python %s (%s)... " "$PYTHON_VERSION" "$ARCH"

    TMPFILE=$(mktemp /tmp/harvest_python.XXXXXX.tar.gz)
    curl -fSL --progress-bar -o "$TMPFILE" "$URL" || fail "Nedladdning misslyckades: $URL"

    mkdir -p "$PYTHON_DIR"
    tar -xzf "$TMPFILE" -C "$PYTHON_DIR" || fail "Extraktion misslyckades"
    rm -f "$TMPFILE"

    # Verifiera
    if [ ! -x "$PYTHON_BIN" ]; then
        fail "Python-binar saknas efter extraktion: $PYTHON_BIN"
    fi

    ok "installerad ✓"
fi

# ──────────────────────────────────────
# [2/6] Virtual environment + dependencies
# ──────────────────────────────────────
step 2 "Venv + dependencies"

NEED_PIP=false

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "Kunde inte skapa venv"
    NEED_PIP=true
fi

# Kolla om requirements.txt har ändrats sedan senaste install
REQ_HASH=$(shasum "$SCRIPT_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1)
STORED_HASH=""
if [ -f "$VENV_DIR/.requirements_hash" ]; then
    STORED_HASH=$(cat "$VENV_DIR/.requirements_hash")
fi

if [ "$NEED_PIP" = true ] || [ "$REQ_HASH" != "$STORED_HASH" ]; then
    "$VENV_PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt" || fail "pip install misslyckades"
    echo "$REQ_HASH" > "$VENV_DIR/.requirements_hash"
    ok "installerat ✓"
else
    skip
fi

# ──────────────────────────────────────
# [3/6] Harvest OAuth
# ──────────────────────────────────────
step 3 "Harvest OAuth"

if [ -f "$HARVEST_TOKEN" ]; then
    skip
else
    echo ""
    echo "   Webblasaren oppnas. Valj ${BOLD}HARVEST${NC} nar du far fragan."
    "$VENV_PYTHON" "$SCRIPT_DIR/harvest_auth.py" || fail "Harvest OAuth misslyckades"
    ok "token sparad ✓"
fi

# ──────────────────────────────────────
# [4/6] Forecast OAuth
# ──────────────────────────────────────
step 4 "Forecast OAuth"

if [ -f "$FORECAST_TOKEN" ]; then
    skip
else
    echo ""
    echo "   Webblasaren oppnas. Valj ${BOLD}FORECAST${NC} den har gangen."
    "$VENV_PYTHON" "$SCRIPT_DIR/harvest_auth.py" forecast || fail "Forecast OAuth misslyckades"
    ok "token sparad ✓"
fi

# ──────────────────────────────────────
# [5/6] Claude Desktop MCP
# ──────────────────────────────────────
step 5 "Claude Desktop MCP"

if [ -f "$CLAUDE_CONFIG" ] && grep -q '"harvest"' "$CLAUDE_CONFIG" 2>/dev/null; then
    skip
else
    if [ ! -f "$CLAUDE_CONFIG" ]; then
        mkdir -p "$(dirname "$CLAUDE_CONFIG")"
        echo '{}' > "$CLAUDE_CONFIG"
    fi

    "$VENV_PYTHON" -c "
import json, sys
config_path, python_path, mcp_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(config_path, 'r') as f:
    config = json.load(f)
config.setdefault('mcpServers', {})
config['mcpServers']['harvest'] = {
    'command': python_path,
    'args': [mcp_path]
}
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
" "$CLAUDE_CONFIG" "$VENV_PYTHON" "$SCRIPT_DIR/harvest_mcp.py" || fail "MCP-registrering misslyckades"

    ok "registrerad ✓"
fi

# ──────────────────────────────────────
# [6/6] Verifiering
# ──────────────────────────────────────
step 6 "Verifiering"

"$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from harvest_auth import load_config
from harvest_client import HarvestClient
config = load_config()
c = HarvestClient(config['harvest'])
me = c._request('GET', '/users/me')
print(f\"   Inloggad som: {me['first_name']} {me['last_name']}\")
" || fail "Verifiering misslyckades — kontrollera tokens"

ok "allt fungerar ✓"

# ──────────────────────────────────────
# Klart
# ──────────────────────────────────────
echo ""
echo "${GREEN}${BOLD}Installation klar!${NC}"
echo ""
echo "  Starta om Claude Desktop for att aktivera HarvestMCP."
echo "  Prova sedan: ${BOLD}Visa teamets belaggning denna vecka${NC}"
echo ""
