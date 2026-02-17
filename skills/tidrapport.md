# /tidrapport — Automatisk tidrapportering

Skill for att bygga och posta veckans tidrapport baserat pa Forecast-planering och MyMemory-kontext.

## Regler

1. **Forecast = Verkligheten** — Forecast-planeringen ar grunden for alla poster
2. **Alla poster kraver kommentarer** — inga tomma notes, generera fran MyMemory eller beskriv aktiviteten generellt
3. **Avvikelser flaggas** — om MyMemory visar annat an Forecast
4. **Tva lager verifiering** — dialog i skillen + prepare/commit i MCP
5. **Aldrig posta utan godkannande** — anvandaren maste bekrafta innan commit

## Interna projekt

| Aktivitet | Projekt | Projekt-ID | Task | Task-ID |
|-----------|---------|------------|------|---------|
| Moten, standup, 1:1, admin, kompetensutveckling | Not client work | 46186398 | NCW | 25275551 |
| Salj, kundvard, prospektering | Affarsutveckling | 46186432 | Salj | 25275550 |
| Marknadsforing, event | Affarsutveckling | 46186432 | Marknad | 25275553 |

## Steg-for-steg

### Steg 0: Faststall vecka och anvandare

- Fraga vilken vecka (default: innevarande vecka)
- Faststall user_id (default: inloggad anvandare, dvs user_id=0)
- Berakna mandag–fredag for veckan

### Steg 1: Kolla befintliga poster

- `harvest_detailed_time_entries(from_date, to_date, user_id)`
- Om poster redan finns: visa dem och fraga om de ska behallas eller ersattas
- ALDRIG skapa dubbletter

### Steg 2: Hamta Forecast-plan

- `forecast_schedule(start_date, end_date, group_by="person")`
- Extrahera anvandardens planerade projekt och timmar per dag

### Steg 3: Hamta task-IDs per projekt

- `harvest_get_project_tasks(project_id)` for varje unikt projekt fran Forecast
- Mappa ratt task_id till varje entry

### Steg 4: Sok MyMemory — kalender

- `search_by_date_range(start_date, end_date)` for att hitta kalenderhandelser
- `search_lake_metadata(keyword="Calendar Event", field="source_type")`
- Identifiera moten, workshops, kundmoten etc.

### Steg 5: Sok MyMemory — Slack + mail

- `query_vector_memory("aktiviteter och arbete vecka X")` for att hitta kontext
- `search_lake_metadata(keyword="Slack Log", field="source_type")` for Slack
- `search_lake_metadata(keyword="Email Thread", field="source_type")` for mail
- Las relevanta dokument med `read_document_content()` for att generera notes

### Steg 6: Bygg utkast

Bygg komplett tidrapport baserat pa Forecast + MyMemory-kontext:
- En entry per projekt per dag (eller flera om det ar rimligt)
- Alla entries MASTE ha notes
- Interna aktiviteter (moten, admin) laggs pa "Not client work / NCW"
- Resterande tid fordelas enligt Forecast-planen

### Steg 7: Dialogbaserad verifiering

Borja med att erbjuda valet:

```
## Tidrapport vecka X — granskning

Jag har byggt ett utkast med N poster (man–fre).
Totalt: XX.Xh (XX.Xh billable, X.Xh non-billable)

Hur vill du granska?
1. **Dag for dag** — jag visar en dag i taget, du godkanner varje
2. **Visa allt** — se hela veckan pa en gang och godkann
```

#### Alternativ 1: Dag for dag

Visa en dag i taget med tabell:

```
### Mandag YYYY-MM-DD

| Projekt | Task | Timmar | Notes | Kalla |
|---------|------|--------|-------|-------|
| VERA 2026 | Development | 6.0 | Sprint planning, API-implementation | Forecast + Slack |
| Not client work | NCW | 2.0 | Drive standup, 1:1 Emilia | Kalender |
| **Summa** | | **8.0** | | |

Stammer mandag?
```

- OK -> nasta dag
- Andring -> justera, visa igen

#### Alternativ 2: Visa allt

Visa hela veckan i en tabell med summering per dag.

### Steg 8: Posta via prepare/commit

1. `harvest_prepare_timesheet(entries)` -> draft_id + preview
2. Visa draft_id for anvandaren som kvittens
3. Fraga "Ska jag posta till Harvest?"
4. `harvest_commit_timesheet(draft_id)` -> postar till Harvest
5. Rapportera resultat per rad

### Steg 9: Verifiering

- `harvest_detailed_time_entries(from_date, to_date, user_id)`
- Visa slutresultatet som bekraftelse att allt landade ratt
