---
name: tidrapport
description: "Bygg och posta veckans tidrapport till Harvest baserat pa Forecast, MyMemory och tidrapporteringsregler."
---

# /tidrapport — Automatisk tidrapportering

Bygg och posta veckans tidrapport till Harvest baserat pa Forecast, MyMemory och tidrapporteringsregler.

## Principer

LLM:en FAR:
- Fordela timmar baserat pa Forecast-planering
- Justera fordelning baserat pa kalenderhandelser
- Generera notes/kommentarer fran MyMemory-kontext (moten, Slack, mail)
- Matcha kundnamn mellan kallor (kortaste gemensamma prefix)
- Flagga avvikelser mellan Forecast och verklighet
- Tillata dagar over 8h om det ar motiverat

LLM:en FAR INTE:
- Posta tidsposter utan explicit godkannande fran anvandaren
- Skapa dubbletter — befintliga poster maste kollas forst
- Gissa projekt eller tasks som inte finns i Forecast/Harvest
- Dolja vilken kalla en observation kommer ifran
- Anvanda harvest_create_time_entry direkt — ALL skapning via prepare/commit

## Timregel

- Dagar over 8h ar tillatet om det ar motiverat
- Om veckans totala timmar overstiger 45h: visa varning:
  "Veckan summerar till XX.Xh (over 45h). Har du for mycket att gora? Prata med din chef for att fa hjalp med planering."
- Varningen visas i steg 6 (utkast) men blockerar INTE processen

## Interna projekt

| Aktivitet | Projekt | Projekt-ID | Task | Task-ID |
|-----------|---------|------------|------|---------|
| Moten, standup, 1:1, admin, kompetensutveckling | Not client work | 46186398 | NCW | 25275551 |
| Salj, kundvard, prospektering | Affarsutveckling | 46186432 | Salj | 25275550 |
| Marknadsforing, event | Affarsutveckling | 46186432 | Marknad | 25275553 |

## Steg-for-steg-procedur

### Steg 0: Faststall period och identitet

- Anropa `parse_relative_date("denna vecka")` for att fa veckans man-fre
- Om anvandaren anger en annan vecka, anvand den istallet
- Anropa `harvest_find_user("Joakim Ekman")` for att hamta user_id
- Spara: user_id, from_date (mandag), to_date (fredag)

### Steg 1: Kolla befintliga poster

- Anropa `harvest_detailed_time_entries(from_date, to_date, user_id=user_id)`
- Om poster redan finns: visa dem, fraga om skillen ska:
  - **Komplettera** — behalla befintliga + lagg till nya
  - **Ersatta** — ta bort befintliga + skapa nya
- HARDFAIL: Posta ALDRIG dubbletter utan explicit godkannande

### Steg 2: Hamta Forecast-plan

- Anropa `forecast_schedule(start_date=from_date, end_date=to_date, group_by="person")`
- Filtrera till aktuell anvandare
- Spara: projekt -> planerade timmar/dag, totalt per vecka
- Om Forecast returnerar tomt: HARDFAIL med meddelande "Forecast returnerade 0 rader for perioden [datum]-[datum]"

### Steg 3: Hamta task-IDs for varje Forecast-projekt

- For varje projekt fran steg 2: anropa `harvest_get_project_tasks(project_id)`
- Spara mappningen projekt -> tillgangliga tasks
- Valj default-task: forsta billable task, eller "Development" om tillganglig
- Om inget projekt matchar Forecast-projektnamnet i Harvest: lista Harvest-projekt och fraga anvandaren

### Steg 4: Sok MyMemory — kalenderhandelser

- Anropa `search_by_date_range(start_date=from_date, end_date=to_date)` — filtrera pa kalenderhandelser
- Anropa `search_lake_metadata(keyword="Calendar Event", field="source_type")`
- Extrahera: motesnamn, tid, kund/projekt-koppling fran context_summary
- Matcha mot Forecast-projekt via kundnamn (kortaste gemensamma prefix)
- Spara: dag -> moten med projekt-koppling och varaktighet

### Steg 5: Sok MyMemory — Slack + mail for kommentarer

- Anropa `search_by_date_range(start_date=from_date, end_date=to_date)` — filtrera pa Slack och Email
- Anropa `query_vector_memory("[Projektnamn] vecka [X]")` per Forecast-projekt
- Anropa `search_lake_metadata(keyword="Slack Log", field="source_type")` for Slack
- Anropa `search_lake_metadata(keyword="Email Thread", field="source_type")` for mail
- Las relevanta dokument med `read_document_content()` for att generera notes
- Om inget hittas for ett projekt: notes blir tom strang (inte "Inget hittat")

### Steg 6: Bygg utkast

Regler:
1. **Forecast = Verkligheten** — utga fran Forecast-planeringen som grund
2. **Kalenderhandelser justerar** — om ett mote tog tid fran ett projekt, fordela om timmar
3. **Not Client Work kraver kommentar** — generera fran MyMemory-kontext (moten, Slack)
4. **Alla billable-poster bor ha notes** — generera fran MyMemory (vad gjordes?)
5. **Avvikelser flaggas** — om verkligheten (kalender/Slack) avviker fran Forecast, visa explicit

Visa utkastet i detta format:

```
## Tidrapport vecka XX (man-fre)

### Avvikelser Forecast vs Verklighet

| Dag | Forecast | Verklighet | Orsak |
|-----|----------|------------|-------|
| Ons | 8h Kund A | 4h Kund A + 4h internt | Halvdags workshop (kalla: Kalender) |

(Om inga avvikelser: "Inga avvikelser — Forecast foljs rakt av.")

### Utkast

| Dag | Projekt | Task | Timmar | Notes | Kalla |
|-----|---------|------|--------|-------|-------|
| Man | VERA 2026 | Development | 6.0 | Sprint planning + API-implementation | Forecast + Slack |
| Man | Not Client Work | NCW | 2.0 | Drive standup + 1:1 Emilia | Kalender |
| Tis | VERA 2026 | Development | 8.0 | Fortsatt API-arbete, PR review | Forecast + Slack |
| ... | ... | ... | ... | ... | ... |

**Totalt:** 40.0h (32.0h billable, 8.0h non-billable)

Stammer detta? Svara OK for att posta, eller beskriv andringar.
```

Kolumnen "Kalla" visar var datan kommer ifran — aldrig gomma varifrån slutsatsen drogs.

Om veckototalen overstiger 45h, visa varning:
> Veckan summerar till XX.Xh (over 45h). Har du for mycket att gora? Prata med din chef for att fa hjalp med planering.

### Steg 7: Dialogbaserad verifiering

Borja med att erbjuda valet:

```
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

Visa hela veckan i en tabell med summering per dag (formatet fran steg 6).

Posta ALDRIG utan explicit OK.

### Steg 8: Posta via prepare/commit

1. Bygg JSON-lista med alla godkanda entries
2. Anropa `harvest_prepare_timesheet(entries, user_id)` -> draft_id + preview
3. Visa draft_id for anvandaren som kvittens
4. Fraga "Ska jag posta till Harvest?"
5. Anropa `harvest_commit_timesheet(draft_id)` -> postar till Harvest
6. Rapportera resultat per rad

Om befintliga poster ska ersattas (fran steg 1): anropa `harvest_delete_time_entry` for varje gammal post FORST, sedan prepare/commit for de nya.

### Steg 9: Verifiering

- Anropa `harvest_detailed_time_entries(from_date, to_date, user_id=user_id)`
- Visa slutresultatet som bekraftelse
- Jamfor totalt postade timmar med utkastet
- Om mismatch: rapportera explicit vad som saknas
