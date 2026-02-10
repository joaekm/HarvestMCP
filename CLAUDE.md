# HarvestMCP

MCP-server som exponerar Harvest- och Forecast-data som verktyg for Claude Desktop, Cursor och andra AI-verktyg.

## Installation

```bash
git clone git@github.com:joaekm/HarvestMCP.git
cd HarvestMCP
./install.sh
```

Scriptet:
1. Skapar venv och installerar dependencies
2. Kor OAuth for Harvest (valj Harvest i webblasaren)
3. Kor OAuth for Forecast (valj Forecast i webblasaren)
4. Verifierar anslutningen
5. Registrerar MCP-servern i Claude Desktop

## Projektstruktur

```
HarvestMCP/
  config.yaml          # OAuth credentials och API-config
  harvest_auth.py      # OAuth2-flode (initial auth + token refresh)
  harvest_client.py    # API-klienter for Harvest + Forecast
  harvest_mcp.py       # MCP-server med verktyg
  install.sh           # Installationsscript
  requirements.txt     # Python-beroenden
```

## Kommandon

```bash
# Initial OAuth-autentisering
python3 harvest_auth.py            # Harvest
python3 harvest_auth.py forecast   # Forecast

# Testa API-klienterna direkt
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
| forecast_schedule | Vem ar schemalagd pa vilka projekt i Forecast |

## Config

- OAuth credentials i `config.yaml` (delade — kopplade till appen, inte anvandaren)
- Harvest-token: `~/.harvest/token.json`
- Forecast-token: `~/.harvest/forecast_token.json`

## Regler

- Inga hardkodade varden - allt fran config.yaml
- HARDFAIL vid fel, inga tysta fallbacks
- Loggar till `~/.harvest/logs/harvest_mcp.log` (aldrig stdout - reserverat for MCP-protokoll)
