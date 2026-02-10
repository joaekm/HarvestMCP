#!/bin/bash
set -e

# HarvestMCP Installer
# Skapar venv, installerar dependencies, kör OAuth för Harvest + Forecast,
# och registrerar MCP-servern i Claude Desktop.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

echo ""
echo "=== HarvestMCP Installer ==="
echo "Katalog: $SCRIPT_DIR"
echo ""

# 1. Skapa venv och installera dependencies
if [ ! -d "$VENV_DIR" ]; then
    echo ">> Skapar venv..."
    python3 -m venv "$VENV_DIR"
else
    echo ">> venv finns redan, hoppar over."
fi

echo ">> Installerar dependencies..."
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "   Klart."

# 2. OAuth - Harvest
echo ""
echo ">> Steg 1/2: Autentisera mot HARVEST"
echo "   Webblasaren oppnas. Valj HARVEST nar du far fragan."
echo ""
read -p "   Tryck ENTER for att fortsatta..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/harvest_auth.py"

# 3. OAuth - Forecast
echo ""
echo ">> Steg 2/2: Autentisera mot FORECAST"
echo "   Webblasaren oppnas igen. Valj FORECAST den har gangen."
echo ""
read -p "   Tryck ENTER for att fortsatta..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/harvest_auth.py" forecast

# 4. Smoke test
echo ""
echo ">> Verifierar anslutning..."
"$VENV_DIR/bin/python" "$SCRIPT_DIR/harvest_client.py"

# 5. Registrera i Claude Desktop
echo ""
PYTHON_PATH="$VENV_DIR/bin/python"
MCP_PATH="$SCRIPT_DIR/harvest_mcp.py"

if [ -f "$CLAUDE_CONFIG" ]; then
    # Kolla om harvest redan finns
    if grep -q '"harvest"' "$CLAUDE_CONFIG" 2>/dev/null; then
        echo ">> Claude Desktop: 'harvest' redan registrerad, hoppar over."
    else
        echo ">> Registrerar i Claude Desktop..."
        # Använd python för att säkert redigera JSON
        "$VENV_DIR/bin/python" -c "
import json, sys
config_path = sys.argv[1]
python_path = sys.argv[2]
mcp_path = sys.argv[3]
with open(config_path, 'r') as f:
    config = json.load(f)
config.setdefault('mcpServers', {})
config['mcpServers']['harvest'] = {
    'command': python_path,
    'args': [mcp_path]
}
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print('   Klart.')
" "$CLAUDE_CONFIG" "$PYTHON_PATH" "$MCP_PATH"
    fi
else
    echo ">> Claude Desktop config hittades inte."
    echo "   Lagg till manuellt i: $CLAUDE_CONFIG"
    echo ""
    echo '   "harvest": {'
    echo "     \"command\": \"$PYTHON_PATH\","
    echo "     \"args\": [\"$MCP_PATH\"]"
    echo '   }'
fi

echo ""
echo "=== Installation klar! ==="
echo ""
echo "Starta om Claude Desktop for att aktivera HarvestMCP."
echo "Prova sedan: 'Visa teamets belaggning denna vecka'"
echo ""
