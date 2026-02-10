# HarvestMCP

MCP-server som exponerar Harvest-tidsrapporteringsdata som verktyg for Claude Desktop, Cursor och andra AI-verktyg.

## Projektstruktur

```
HarvestMCP/
  config.yaml          # Harvest OAuth credentials och API-config
  harvest_auth.py      # OAuth2-flode (initial auth + token refresh)
  harvest_client.py    # API-klient (pagination, rate limits, auth)
  harvest_mcp.py       # MCP-server med verktyg
  requirements.txt     # Python-beroenden
```

## Kommandon

```bash
# Initial OAuth-autentisering (oppnar webblasare)
python3 harvest_auth.py

# Testa API-klienten direkt
python3 harvest_client.py

# Starta MCP-servern (gors normalt av Claude Desktop)
python3 harvest_mcp.py
```

## MCP-verktyg

| Verktyg | Beskrivning |
|---------|-------------|
| harvest_team_utilization | Teamets belaggning: billable/non-billable, kapacitet, util% |
| harvest_who_works_on_what | Vem jobbar med vad, grupperat per projekt eller person |
| harvest_time_summary | Flexibel tidsrapport med filter (projekt, kund, person) |
| harvest_list_projects | Lista projekt med ID (for filtrering) |
| harvest_list_users | Lista anvandare med ID (for filtrering) |

## Claude Desktop-registrering

```json
{
  "mcpServers": {
    "harvest": {
      "command": "python3",
      "args": ["/Users/jekman/Projects/HarvestMCP/harvest_mcp.py"]
    }
  }
}
```

## Config

Credentials i `config.yaml`. Token sparas i `~/.harvest/token.json` efter forsta auth.

## Regler

- Inga hardkodade varden - allt fran config.yaml
- HARDFAIL vid fel, inga tysta fallbacks
- Loggar till `~/.harvest/logs/harvest_mcp.log` (aldrig stdout - reserverat for MCP-protokoll)
