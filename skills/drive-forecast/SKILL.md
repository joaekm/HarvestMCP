---
name: drive-forecast
description: "Korsreferera Harvest/Forecast mot MyMemory for Drive-teamet. Producerar strukturerad kundtabell med bevisniva per signal."
---

# Drive Forecast — Kundprognos

## Aktivering

Denna skill aktiveras med `/drive-forecast`.

## Syfte

Korsreferera hard data (Harvest/Forecast) mot mjuk data (MyMemory: Slack, mail, moten, transkript) och producera en strukturerad kundtabell. Anvandaren drar slutsatserna — skillen presenterar bara observationer med kalla och datum.

## Principer

### LLM:en FAR:

- Jamfora tal mellan kallor (Harvest vs Forecast vs MyMemory)
- Matcha kundnamn mellan kallor
- Rakna forekomster (hur ofta namns nagot)
- Tidsjamforelser (senaste namning var X dagar sedan)
- Presentera observationer med kalla och datum

### LLM:en FAR INTE:

- Dra slutsatser om *varfor* nagot hander
- Presentera en enskild datapunkt som ett monster
- Skriva berattande prosa om kundrelationer
- Gissa roller (KA/PL) baserat pa omnamningar
- Tolka Slack-entusiasm som bekraftad affarsmojlighet
- Anvanda adjektiv eller monster-ord (konsekvent, tydligt, genomgaende, starkt)
- Skriva "Observationer" eller sammanfattande avsnitt efter tabellerna

### Bevisniva per signal

Varje kundobservation markeras med bevisniva:

| Niva | Krav | Markering |
|------|------|-----------|
| Bekraftad | 3+ oberoende kallor | *** |
| Stark | 2 oberoende kallor | ** |
| Svag | 1 kalla | * |

## Sokprogram

Exekvera stegen i ordning. Varje steg bygger pa foregaende.

### Steg 0: Faststall datum

Anropa `parse_relative_date("idag")` FORST. Anvand returnerat datum som bas for alla efterfoljande API-anrop. Hardkoda ALDRIG artal.

### Steg 1: Hard data — Teamets belaggning och plan

Anropa `harvest_get_team("drive")` for att hamta Drive-teamets medlemmar med user_id, namn och kapacitet. Detta ger den aktuella teamlistan dynamiskt — ingen hardkodad lista att underhalla.

Harvest-verktyg:

- `harvest_team_utilization` senaste 4 veckor (berakna from_date/to_date fran steg 0) — FILTRERA resultatet till bara Drive-teamet
- `forecast_schedule` kommande 4 veckor (berakna start_date/end_date fran steg 0), `group_by="person"` — FILTRERA till Drive-teamet

**VIKTIGT:** Om en person fran Drive-teamlistan saknas i utilization-data, rapportera det explicit som "SAKNAS I HARVEST" — gissa inte.

**VIKTIGT:** Om Forecast returnerar tom data, rapportera det explicit: "Forecast returnerade 0 rader for perioden [datum]-[datum]". Prova INTE att tolka bort problemet.

Spara: person -> billable%, kapacitet, planerade timmar (Harvest) + allokerade timmar (Forecast)

### Steg 2: Hard data — Projekt och kunder

Harvest-verktyg:

- `harvest_time_summary` senaste 4 veckor, `group_by="project"`

**FILTRERING:** Resultatet innehaller ALLA projekt i bolaget. Du MASTE filtrera till bara projekt dar minst en Drive-teammedlem har rapporterat tid. Rakna bara timmar fran Drive-teammedlemmar, inte hela projektets timmar.

**OBS:** `harvest_time_summary(group_by="project")` visar per projekt vilka personer som jobbat dar. Anvand Drive-teamlistan fran steg 1 for att filtrera.

Spara: kund -> projekt -> faktiska timmar (BARA Drive-teamets), billable/non-billable

### Steg 3: Metadata-sokning — Slack/Mail/Kalender

MyMemory-verktyg:

- `search_lake_metadata(keyword="Slack Log", field="source_type")` — extrahera kundnamn fran `context_summary`, INTE fran dokumentinnehall
- `search_lake_metadata(keyword="Email Thread", field="source_type")` — samma
- `search_lake_metadata(keyword="Calendar Event", field="source_type")` — samma
- `search_by_date_range` senaste 30 dagar — fanga dokument med tidsangivelse

Filtrera pa relevanta kanaler via filnamn: `salj_sales`, `se_drive`, `ledningsgruppen`

Spara: kundnamn -> kalla, datum, sammanfattning (fran context_summary)

### Steg 4: Vektorsokning — Kundnamn som soktermer

Anvand kundnamn fran steg 1-3 som soktermer i MyMemory:

- `query_vector_memory("[Kundnamn]")` — EN sokning per kund, kundniva, inte personniva
- Sok ALLA kunder fran steg 2, inte bara de som saknar MyMemory-data
- Sok ALLA kunder som dok upp i steg 3 men INTE i Harvest — de ar potentiellt oplanerade mojligheter
- Sok kunder med stor allokering i Harvest men ingen metadata-traff i steg 3

Vektorsokningen returnerar chunks inklusive transkript-parts. Notera kalla (filnamn) och datum. Las INTE hela dokument — anvand `context_summary` eller chunk-texten som redan returnerats.

### Steg 5: Korsreferering

Bygg en sammanslagen kundlista med tre kategorier:

- Harvest-kunder MED MyMemory-aktivitet senaste 30d
- MyMemory-kunder UTAN Harvest-projekt
- Harvest-kunder UTAN MyMemory-aktivitet senaste 30d

**NAMNMATCHNING:** Harvest anvander ofta formella bolagsnamn ("Besqab Projekt och Fastigheter AB", "Sv. Kommunalarbetareforbundet") medan MyMemory anvander kortnamn ("Besqab", "Kommunal"). Matcha pa det kortaste gemensamma prefixet — "Besqab" matchar "Besqab Projekt och Fastigheter AB". Om du ar osaker, rakna det som en match snarare an att missa den.

### Steg 6: Strukturerad output

Presentera resultatet som tabeller. ALDRIG lopande text. INGA avslutande observationer eller sammanfattningar.

Kolumnerna ska vara exakta:

- "Faktiska timmar 4v" = Harvest-data (bakat)
- "Forecast 4v" = Forecast-data (framat)
- Blanda ALDRIG ihop dessa. Om Forecast-data saknas, skriv "—" inte "0h".

**AGGREGERING:** Om en person har 3+ kunder med <20h Forecast inom samma tjansteomrade, aggregera till en rad: "Marc Matomo-kunder (7 st): 32h faktiskt, 100h Forecast". Lista kundnamnen i parentes. Steg 4 vektorsokning och Steg 7 avvikelseanalys skippar aggregerade kunder.

```
## Kundoversikt Drive — [datum]

Fakturerat: [from_date] – [to_date] (4v bakat). Forecast: [from_date] – [to_date] (4v framat). MyMemory: senaste 30 dagar.

### Kunder med Harvest-projekt + MyMemory-aktivitet (senaste 30d)

| Kund | Harvest-projekt | Faktiska timmar [from]-[to] | Forecast [from]-[to] | Senaste MyMemory-signal | Kalla | Bevis |
|------|----------------|---------------------------|---------------------|------------------------|-------|-------|
| Clarendo | VERA 2026 | 45h | 201h | Avtal undertecknat 11 feb | Mail + Slack | ** |

### Kunder med MyMemory-aktivitet (senaste 30d) UTAN Harvest-projekt

| Kund | Senaste signal | Kalla | Datum | Bevis |
|------|---------------|-------|-------|-------|
| Klovern | Nykundsm ote via Balder-referens | Slack salj | 13 jan | * |

### Harvest-projekt UTAN MyMemory-aktivitet (senaste 30d)

| Kund | Projekt | Faktiska timmar [from]-[to] | Forecast [from]-[to] | Senaste namning i MyMemory |
|------|---------|---------------------------|---------------------|---------------------------|
| TRS | Utveckling 2026 | 59h | 45h | Ingen hittad |

### Kundkontext — alla kunder fran tabellerna ovan

| Kund | Nulage (fakta) | Nasta handelse (fran MyMemory) | Risksignal (fakta) |
|------|----------------|-------------------------------|-------------------|
| Clarendo | 45h fakturerat 4v, avtal signerat 11 feb | Kickoff namnd i Slack 12 feb | — |
| Klovern | 0h i Harvest | Mote omnamnt i Slack salj 13 jan | Ingen Forecast-allokering |
| TRS | 59h fakturerat 4v | — | 0 MyMemory-traffar senaste 30d |

### Teambelaggning Drive — [from_date] – [to_date] (4v bakat), Forecast [from_date] – [to_date] (4v framat)

| Person | Billable% [from]-[to] | Forecast [from]-[to] |
|--------|----------------------|---------------------|
| Erik | 38% | 91h |
```

**Kundkontext-regler:**

- "Nulage" = aritmetik fran Harvest + senaste bekraftade handelse med datum
- "Nasta handelse" = omnamnd framtida handelse i MyMemory (mote, kickoff, deadline) med kalla och datum. Om inget finns: "—"
- "Risksignal" = aritmetiska fakta: "0 Forecast-timmar", "0 MyMemory-traffar 30d", "Billable 15%". Inga tolkningar. Om inget: "—"

### Steg 7: Avvikelseanalys — Topp 5

Identifiera de 5 storsta avvikelserna rent aritmetiskt:

- Storst gap: Forecast-timmar vs faktiska timmar (kund/person)
- Hog Forecast, 0h faktiskt (kund)
- Lagst billable% (person)

**For varje avvikelse:** Sok i MyMemory med `search_graph_nodes` och `query_vector_memory` pa personnamn + kundnamn.

```
### Avvikelser — Topp 5

| # | Avvikelse (aritmetik) | MyMemory-kontext | Kallor |
|---|----------------------|-----------------|--------|
| 1 | Johan: billable 15%, SEB 0h faktiskt / 140h Forecast | Graf: "Designuppdraget SEB PWM" (nod). Slack salj 23 jan: konsultuthyrning. | Graf + Slack |
| 2 | Joakim: billable 1%, 160h Forecast | Graf: 6 roller (Chef, Rekrytering, Resursplanering). Adda techdemo 16 feb. | Graf |
| 3 | Magnit: 80h Forecast, 0h faktiskt | 0 traffar i MyMemory | — |
```

**REGLER Steg 7:**

- Avvikelsen ar BARA aritmetik: "X% vs Y%", "0h vs Zh"
- MyMemory-kontext ar BARA vad sokningarna returnerade: nod-namn, context-text, filnamn, datum
- Om sokningen ger 0 traffar: skriv "0 traffar i MyMemory". ALDRIG "troligen", "formodligen", "har inte startat"
- Inga slutsatser. Inga forklaringar. Inga kopplingar mellan avvikelse och kontext
- Kolumnen "MyMemory-kontext" ar en LISTA av fakta, inte en mening som binder ihop dem

Avsluta efter tabellen. Ingen analys. Ingen "vill du att jag tittar narmare pa...". Inget avslutande stycke.

## Tillgangliga Harvest/Forecast-verktyg (referens)

| Verktyg | Syfte | Nyckelparametrar |
|---------|-------|-----------------|
| `harvest_time_summary` | **Primart verktyg** for tidsoversikter | `group_by`: "summary" / "project" / "person". Filter: `project_id`, `user_id`, `client_id` |
| `harvest_team_utilization` | Belaggning per person | `from_date`, `to_date` |
| `harvest_get_team` | Hamta teammedlemmar med ID/namn/kapacitet | `query` (fuzzy namn) |
| `harvest_list_teams` | Lista alla team/roller | — |
| `harvest_find_user` | Sok anvandare pa namn (fuzzy) | `query` |
| `harvest_find_project` | Sok projekt pa namn (fuzzy) | `query` |
| `harvest_list_projects` | Lista alla projekt (DYRT) | `active_only` |
| `harvest_detailed_time_entries` | Enskilda tidsposter med kommentarer. DYRT. | Filter: `user_id`, `project_id` |
| `forecast_schedule` | Planerad allokering | `group_by`: "person" / "project" |
