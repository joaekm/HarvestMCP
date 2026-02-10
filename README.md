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

Öppna mappen du fått (t.ex. via mail eller Slack) och lägg den på Skrivbordet. Kör sedan i terminalen:

```bash
cd ~/Desktop/HarvestMCP
./install.sh
```

**3. Logga in på Harvest**

Installationsskriptet öppnar din webbläsare automatiskt — **två gånger**:

- **Första gången:** Välj **Harvest** och logga in med dina vanliga Harvest-uppgifter
- **Andra gången:** Välj **Forecast** och logga in igen

Gå tillbaka till terminalen och tryck Enter mellan varje steg när skriptet ber om det.

**4. Starta om Claude Desktop**

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
