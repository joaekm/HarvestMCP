# HarvestMCP

MCP-server som exponerar Harvest- och Forecast-data som verktyg for Claude Desktop, Cursor och andra AI-verktyg.

## Smart anvandning av verktygen (for AI-agenter)

Verktygen ar context-optimerade med `max_rows` och kompakta format.
Folj dessa regler for att minimera token-forbrukning:

1. **Borja med oversikt, zooma in vid behov**
   - `harvest_team_utilization` — snabb oversikt, alltid kompakt
   - `harvest_time_summary` (group_by="summary") — aggregerad per projekt
   - Anropa `harvest_detailed_time_entries` BARA nar du behover enskilda poster

2. **Anvand filter — hamta aldrig all data**
   - Ange `project_id` eller `user_id` nar du kan
   - Smal datumintervall (en vecka, inte en manad)

3. **Lat max_rows vara default (30)**
   - Oka bara om anvandaren explicit behover mer
   - Output visar alltid totaler aven vid trunkering

4. **Anvand find-verktygen for ID-lookup — INTE list-verktygen**
   - `harvest_find_project("besqab")` → returnerar bara matchande projekt (1-2 rader)
   - `harvest_find_user("anna")` → returnerar bara matchande anvandare (1-2 rader)
   - `harvest_list_projects`/`harvest_list_users` — BARA om du behover HELA listan

5. **Valj ratt group_by i harvest_time_summary**
   - "summary" (default): minst output, en rad per projekt
   - "project": per projekt med personuppdelning
   - "person": per person med projektuppdelning

## Installation

```bash
git clone git@github.com:joaekm/HarvestMCP.git
cd HarvestMCP
./install.sh
```

Scriptet ar idempotent — kor om utan problem. Hoppar over steg som redan ar klara:
1. Skapar venv och installerar dependencies (hoppar over om oforandrat)
2. Harvest OAuth (hoppar over om token finns)
3. Forecast OAuth (hoppar over om token finns)
4. Registrerar MCP-servern i Claude Desktop (hoppar over om redan registrerad)
5. Verifierar anslutningen mot Harvest API

## Projektstruktur

```
HarvestMCP/
  config.yaml          # OAuth credentials och API-config
  harvest_auth.py      # OAuth2-flode (initial auth + token refresh)
  harvest_client.py    # API-klienter for Harvest + Forecast
  harvest_mcp.py       # MCP-server med verktyg
  install.sh           # Installationsscript
  requirements.txt     # Python-beroenden
  skills/                        # Claude Code skills
    tidrapport/SKILL.md          # /tidrapport — automatisk tidrapportering
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

### Oversikt & analys (borja har)

| Verktyg | Beskrivning | Context-kostnad |
|---------|-------------|-----------------|
| harvest_team_utilization | Teamets belaggning: billable/non-billable, util% | Lag (en rad/person) |
| harvest_time_summary | Flexibel tidsrapport med group_by och filter | Lag-medel |
| forecast_schedule | Vem ar schemalagd pa vilka projekt i Forecast | Lag |

### Detaljer (anvand med filter)

| Verktyg | Beskrivning | Context-kostnad |
|---------|-------------|-----------------|
| harvest_detailed_time_entries | Enskilda tidsposter med entry_id och kommentarer | HOG — anvand filter + max_rows |

### Lookup (anvand find-verktygen forst!)

| Verktyg | Beskrivning | Context-kostnad |
|---------|-------------|-----------------|
| harvest_find_project | Fuzzy-sok projekt pa namn/kund → ID (1-3 rader) | MINIMAL |
| harvest_find_user | Fuzzy-sok anvandare pa namn → ID (1-3 rader) | MINIMAL |
| harvest_list_projects | Lista ALLA projekt (anvand find forst) | Medel |
| harvest_list_users | Lista ALLA anvandare (anvand find forst) | Lag-medel |
| harvest_get_project_tasks | Tasks for ett projekt (behovs for task_id) | Lag |

### Skrivoperationer

| Verktyg | Beskrivning |
|---------|-------------|
| harvest_prepare_timesheet | Skapa utkast av tidsposter (returnerar draft_id) |
| harvest_commit_timesheet | Posta granskat utkast till Harvest |
| harvest_update_time_entry | Uppdatera timmar/notes pa befintlig post |
| harvest_delete_time_entry | Ta bort en tidspost |

### System

| Verktyg | Beskrivning |
|---------|-------------|
| harvest_self_update | Uppdatera fran GitHub: pull + dependencies |

## Config

- OAuth credentials i `config.yaml` (delade — kopplade till appen, inte anvandaren)
- Harvest-token: `~/.harvest/token.json`
- Forecast-token: `~/.harvest/forecast_token.json`

## Skills

Skills foljer Claude Code-formatet: en mapp per skill med `SKILL.md` som entrypoint.
Kallkoden lever i `skills/` i repot. Anvandaren kopierar mappen manuellt
till `~/.claude/skills/` och registrerar i Claude Code Settings.

Claude Code ska ALDRIG kopiera eller installera skills — det gor anvandaren sjalv.

Arbetssatt vid andring av en skill:
1. Redigera `skills/<namn>/SKILL.md` i repot
2. Committa
3. Anvandaren kopierar mappen till `~/.claude/skills/` sjalv

| Skill | Beskrivning |
|-------|-------------|
| /tidrapport | Bygger och postar veckans tidrapport via Forecast + MyMemory |

## Regler

- Inga hardkodade varden - allt fran config.yaml
- HARDFAIL vid fel, inga tysta fallbacks
- Loggar till `~/.harvest/logs/harvest_mcp.log` (aldrig stdout - reserverat for MCP-protokoll)
