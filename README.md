# HarvestMCP

MCP-server som ger Claude Desktop tillgång till Harvest- och Forecast-data (tidsrapporter, beläggning, schemaläggning).

## Installera HarvestMCP

### Vad du behöver
- **macOS** med Python 3 installerat (följer med macOS)
- **Claude Desktop** installerat ([ladda ner här](https://claude.ai/download))
- Tillgång till företagets Harvest-konto

### Steg för steg

**1. Öppna Terminal**

Tryck `Cmd + Mellanslag`, skriv **Terminal** och tryck Enter. Ett svart/vitt fönster öppnas — det är terminalen.

**2. Kör installationen**

Ladda ner repot som ZIP från GitHub:

1. Gå till repot på GitHub
2. Klicka på den gröna knappen **Code** → **Download ZIP**
3. Packa upp ZIP-filen och flytta mappen till en valfri plats, till exempel:

| Alternativ | Sökväg | Beskrivning |
|------------|--------|-------------|
| **A** | `~/MCP/HarvestMCP` | Dedikerad MCP-mapp — bra om du har flera MCP-servrar |
| **B** | `~/HarvestMCP` | Direkt i hemkatalogen — enklast möjligt |
| **C** | `~/Library/HarvestMCP` | macOS-konvention — dold i Finder, ur vägen |

> **Tips:** Den uppackade mappen heter `HarvestMCP-main` — byt gärna namn till `HarvestMCP`.

Skapa mappen (om den inte finns) och flytta dit. Exempel med alternativ A:

```bash
mkdir -p ~/MCP
mv ~/Downloads/HarvestMCP-main ~/MCP/HarvestMCP
```

**3. Skapa config.yaml**

Filen `config.yaml` följer inte med i ZIP-nedladdningen. Skapa den i HarvestMCP-mappen:

```bash
cat > config.yaml << 'EOF'
# HarvestMCP Configuration

harvest:
  client_id: "4npFjmvY_YXMzygxlyZA4KFG"
  client_secret: "YjQFZrEyxJFTzT-U1x0Dl9TsY0TDex8kmxW1JwYuUbxsh4BcXnu-Jzc2zkZ0UfTxTqnlNfD0TGlk-pIBedrGVw"
  redirect_uri: "http://localhost:8080/callback"
  token_path: "~/.harvest/token.json"
  authorize_url: "https://id.getharvest.com/oauth2/authorize"
  token_url: "https://id.getharvest.com/api/v2/oauth2/token"
  api_base_url: "https://api.harvestapp.com/v2"
  user_agent: "HarvestMCP (joakim.ekman@digitalist.se)"

forecast:
  client_id: "4npFjmvY_YXMzygxlyZA4KFG"
  client_secret: "YjQFZrEyxJFTzT-U1x0Dl9TsY0TDex8kmxW1JwYuUbxsh4BcXnu-Jzc2zkZ0UfTxTqnlNfD0TGlk-pIBedrGVw"
  redirect_uri: "http://localhost:8080/callback"
  token_path: "~/.harvest/forecast_token.json"
  authorize_url: "https://id.getharvest.com/oauth2/authorize"
  token_url: "https://id.getharvest.com/api/v2/oauth2/token"
  api_base_url: "https://api.forecastapp.com"
  user_agent: "HarvestMCP (joakim.ekman@digitalist.se)"
EOF
```

**4. Kör installationen**

```bash
cd ~/MCP/HarvestMCP
./install.sh
```

**5. Logga in på Harvest**

Installationsskriptet öppnar din webbläsare automatiskt — **två gånger**:

- **Första gången:** Välj **Harvest** och logga in med dina vanliga Harvest-uppgifter
- **Andra gången:** Välj **Forecast** och logga in igen

Gå tillbaka till terminalen och tryck Enter mellan varje steg när skriptet ber om det.

**6. Starta om Claude Desktop**

Skriptet registrerar HarvestMCP automatiskt i Claude Desktop. Men du måste **stänga och öppna Claude Desktop** för att det ska börja fungera:

- Högerklicka på Claude-ikonen i Dock → **Avsluta**
- Öppna Claude Desktop igen

### Testa att det fungerar

Skriv i Claude Desktop:

> *"Visa teamets beläggning denna vecka"*

Om du får en tabell med namn och timmar — allt fungerar!

### Felsökning

| Problem | Lösning |
|---------|---------|
| Webbläsaren öppnas inte | Kopiera URL:en som visas i terminalen och klistra in i webbläsaren manuellt |
| Claude Desktop visar inga Harvest-verktyg | Starta om Claude Desktop. Kontrollera under Inställningar → Developer → MCP Servers att "harvest" finns med |
