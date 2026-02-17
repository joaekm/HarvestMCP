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
  skills/              # Claude Code skills (kopiera till ~/.claude/skills/)
    tidrapport.md      # /tidrapport — automatisk tidrapportering
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
| harvest_detailed_time_entries | Detaljerade tidsposter med kommentarer/notes |
| harvest_get_project_tasks | Lista tillgangliga tasks for ett projekt (behövs for task_id) |
| harvest_prepare_timesheet | Skapa utkast av tidsposter for granskning (returnerar draft_id) |
| harvest_commit_timesheet | Posta granskat utkast till Harvest (kräver draft_id) |
| harvest_update_time_entry | Uppdatera timmar/notes pa befintlig post (PATCH /time_entries/{id}) |
| harvest_delete_time_entry | Ta bort en tidspost (DELETE /time_entries/{id}) |
| forecast_schedule | Vem ar schemalagd pa vilka projekt i Forecast |

## Config

- OAuth credentials i `config.yaml` (delade — kopplade till appen, inte anvandaren)
- Harvest-token: `~/.harvest/token.json`
- Forecast-token: `~/.harvest/forecast_token.json`

## Skills

Skill-filer lever i `skills/` i repot och kopieras till `~/.claude/skills/` for att aktiveras:

```bash
cp skills/*.md ~/.claude/skills/
```

| Skill | Beskrivning |
|-------|-------------|
| /tidrapport | Bygger och postar veckans tidrapport via Forecast + MyMemory |

## Regler

- Inga hardkodade varden - allt fran config.yaml
- HARDFAIL vid fel, inga tysta fallbacks
- Loggar till `~/.harvest/logs/harvest_mcp.log` (aldrig stdout - reserverat for MCP-protokoll)
