---
name: drive-budget
description: "Veckovis budgetuppfoljning och prognos for Drive-enheten. Hamtar data fran Harvest, Forecast, MyMemory och DigiClients-snapshot. Producerar bedomningsunderlag och Excel-fil redo for uppladdning i DigiClients."
---

# Drive Budget — Veckovis budgetcykel

## Aktivering

Denna skill aktiveras med `/drive-budget`.

## Syfte

Producera ett komplett budgetunderlag per kund, per manad, redo att matas in i DigiClients. Processen minimerar manuellt arbete genom att automatisera datahamtning och korsreferering, och begransar anvandarens insats till bedomningar som kraver manskligt omdome.

## Designprinciper

### Anvandaren ska:

- Bekrafta eller justera — aldrig rakna
- Svara muntligt pa specifika fragor — aldrig fylla i tomma falt
- Ladda upp en fardig fil — aldrig mata in data manuellt i DigiClients

### Skillen ska:

- Hamta all tillganglig data automatiskt
- Anvanda forra veckans bedomningar som baseline (via MyMemory)
- Flagga bara det som andrats eller kraver beslut
- Producera en Excel-fil som matchar DigiClients importformat

### Skillen FAR INTE:

- Gissa budgetsiffror utan datakalla
- Satta pipeline-steg eller sannolikhet utan anvandarens bekraftelse
- Anta att Forecast-timmar x timtaxa = budget (det ar en prognos, inte ett beslut)
- Producera output utan att alla bedomningsfragor besvarats

## Kontext

### Organisationen

- Konsultbolag, ~50 personer, ~80 MSEK omsattning, ~2% marginal
- Drive-enheten: 9 konsulter, UX/design/strategi/arkitektur
- Mal: 70% billable. Nulage: ~40%
- Varje ej fakturerad timme ater direkt av marginalen

### DigiClients — vad som ska fyllas i

DigiClients (digiclients.digitalist.tools) har tre vyer per kund:

**Vy 1: Manadsdata (kundniva)**

| Falt | Per manad (jan-dec) |
|------|-------------------|
| Budget | SEK |
| Upsell | SEK |

Beraknade falt (fylls inte i): Mojlig intakt, Fakturerat, Skillnad Budget vs Fakturerat.

**Vy 2: Projektbudgetar (projektniva)**

| Falt | Varde |
|------|-------|
| Projekt | Dropdown (Harvest-projekt) |
| Startdatum | Datum |
| Slutdatum | Datum |
| Timmar per manad | Per manad inom start-slut |
| Belopp (SEK) per manad | Per manad inom start-slut |

**Vy 3: Pipeline (prospects)**

| Falt | Varde |
|------|-------|
| Kundnamn | Text |
| Board | Offentlig upphandling / AI-salj / Ovrigt salj |
| Steg | Lead (10%) / Kvalificerad (25%) / Offert (50%) / Forhandling (75%) |
| Varde | SEK |

### Datakallor

| Kalla | Innehall | Atkomst |
|-------|----------|---------|
| Harvest | Fakturerade timmar, timtaxor, projektbudgetar | MCP: harvest_* |
| Forecast | Planerade timmar per person/projekt | MCP: forecast_schedule |
| MyMemory | Slack, mail, moten, avtal, tidigare bedomningar | MCP: query_vector_memory, search_lake_metadata, search_graph_nodes, search_by_date_range |
| DigiClients-snapshot | Nulage i DigiClients (JSON-fil fran Playwright-scraper) | Fil i MyMemory eller uppladdad |

## Sokprogram

### Steg 0: Faststall datum, las baseline och identifiera luckor

1. Anropa `parse_relative_date("idag")` — anvand som bas for alla datumberakningar
2. Sok i MyMemory efter senaste drive-budget-output: `query_vector_memory("drive-budget veckorapport")`
3. Sok efter senaste DigiClients-snapshot: `query_vector_memory("digiclients snapshot")`
4. Om baseline finns: ladda den. Alla bedomningar utgar fran forra veckans varden.
5. Om ingen baseline finns: detta ar forsta korningen. Alla kunder kraver full bedomning.

**Gap analysis fran snapshot:**

DigiClients-snapshoten (JSON fran Playwright) visar exakt vad som redan finns i systemet och vad som saknas. Anvand den som att-gora-lista:

- `budget_status: "Saknar budget"` -> kunden har inga projektbudgetar alls. Kraver full budget.
- `budget_status: "X (av Y)"` dar X < Y -> kunden har delvis ifylld budget. Identifiera vilka projekt som saknas genom att jamfora `project_budgets` i snapshoten mot Harvest-projekt.
- `budget_status: "X (av X)"` -> alla projekt har budget. Kontrollera bara att siffrorna fortfarande stammer.
- Pipeline-data i snapshoten visar vilka prospects som redan finns i DigiClients. Jamfor mot kanda pipeline-kunder (fran MyMemory/Harvest) — de som saknas ska laggas till.

**Prioritering:**

Kunder som aktivt fakturerar men saknar budget ar hogst prioritet — de representerar intakter som inte syns i styrverktyget. Sortera att-gora-listan efter fakturerade timmar (fallande) sa att de viktigaste luckorna fylls forst.

**Undvik dubbelarbete:**

Producera BARA data for luckor. Om en kund redan har korrekta budgetar i DigiClients, skippa den (eller flagga under sektion 1 "Bekrafta"). Excel-outputen ska enbart innehalla rader som behover matas in eller uppdateras.

### Steg 1: Hamta hard data

Hamta Drive-teamets medlemmar:

- `harvest_get_team("drive")` -> teammedlemmar med user_id, namn, kapacitet

Hamta Harvest/Forecast-data:

- `harvest_team_utilization` senaste 4 veckor
- `forecast_schedule` kommande 4 veckor, `group_by="person"`
- `harvest_time_summary` senaste 4 veckor, `group_by="project"`

Filtrera allt till Drive-teamets medlemmar.

Komplettera med:

- `harvest_find_project` per kundnamn -> hamta projektbudgetar (totalbelopp i Harvest) och timtaxor
- `harvest_time_summary` per kund senaste 4 veckor (anvand `client_id`-filter) -> fakturerat belopp (timmar x taxa)

### Steg 2: Hamta mjuk data

Exekvera `/drive-forecast`-skillens steg 3-4:

- MyMemory metadata-sokning (Slack, Mail, Kalender)
- Vektorsokning per kundnamn

Fokusera pa:

- Avtalsvarden och avtalsstatus
- Pipeline-rorelser (nya moten, offertforfragningar, avslag)
- Resursforandringar (nya bokningar, omplaneringar)
- Budget-relaterade diskussioner

### Steg 3: Korsreferera och klassificera

Bygg en kundlista. Klassificera varje kund i en av tre kategorier:

**Kategori A: Avtalade kunder med kand budget**

Kriterium: Aktivt Harvest-projekt + kant kontraktsvarde eller Harvest-budget.

-> Budget kan beraknas: kontraktsvarde fordelat over projektperiod.
-> Fraga till anvandaren: bara fordelning (linjart? front/backloaded?)

**Kategori B: Lopande/forvaltningskunder**

Kriterium: Aktivt Harvest-projekt + historiska timmar men inget fast kontraktsvarde.

-> Budget kan projiceras: historisk run-rate x timtaxa.
-> Fraga till anvandaren: stammer run-rate? Forandringar?

**Kategori C: Pipeline-kunder**

Kriterium: MyMemory-aktivitet men inget/litet i Harvest. Eller nyss vunna utan budget.

-> Budget kan inte beraknas. Kraver bedomning av steg, sannolikhet och varde.
-> Fraga till anvandaren: pipeline-steg + uppskattat varde.

### Steg 4: Jamfor med baseline

Om baseline (forra veckans bedomning) finns:

- Identifiera kunder som INTE andrats (Harvest ~ plan, inga nya signaler) -> Sektion 1
- Identifiera kunder med avvikelse eller ny information -> Sektion 2
- Identifiera kunder som kraver extern input -> Sektion 3
- Identifiera pipeline-rorelser -> Sektion 4

Om ingen baseline finns: alla kunder hamnar i sektion 2 eller 3.

### Steg 5: Presentera bedomningsunderlag

Presentera fyra sektioner. Varje sektion ska vara minimal — bara det anvandaren behover for att fatta beslut.

#### Sektion 1: Bekrafta (ingen andring)

```
Dessa kunder har inga avvikelser sedan forra veckan. Budget oforandrad.

| Kund | Budget/man | Kommentar |
|------|-----------|-----------|
| Alkoholhjalpen | 120k | Cenk 100%. Harvest i linje. |
| AFRY | 180k | David. Ny budget 730k fordelad feb-maj. |

-> Bekrafta alla? (Ja / Nej, jag vill justera X)
```

#### Sektion 2: Bedom (avvikelse eller forandring)

```
Dessa kunder har ny information eller avvikelse. Behover din bedomning.

CLARENDO VERA
Budget i DigiClients: 0 kr (ej ifyllt)
Harvest-budget: 1 000 000 kr. Period: feb-maj 2026.
Forecast: 201h kommande 4v (David 104h, Erik 77h, Emilia 20h).
Signal: Avtal signerat 11 feb. Emilia ny PL. Kickoff pagar.
Forslag: 250 000 kr/man linjart feb-maj.
-> Acceptera forslag? Justera?

TRS
Forra veckan: 150k/man.
Harvest senaste 4v: 126h fakturerat.
Signal: Mia saljer in nytt (1-3M). Workshop genomford.
-> Behover du uppdatera med ny info fran Mia om TRS-utokningen?
```

#### Sektion 3: Kolla (information saknas)

```
Dessa kunder kraver att du kollar med nagon.

| Kund | Fraga | Kolla med |
|------|-------|-----------|
| KIA | Salj eller leverans? Projektkod? Varde? | Magnus (ej svarat sedan 11/2) |
| Besqab | Workshops — leder det till lopande uppdrag? Uppskattat varde? | Tim |
| DIGG | Omfattning okand. Kristians bestallning otydlig. | Kristian / Emilia |
```

#### Sektion 4: Pipeline-uppdatering

```
Aktiva prospects. Uppdatera steg och varde.

| Kund | Forra veckan | Ny signal | Steg? | Varde? |
|------|-------------|-----------|-------|--------|
| Besqab | Lead, 500k | Workshops planerade med IT-chef | Kvalificerad? | |
| Pricer | Lead, 800k | 3 moten + strategipresentation | Kvalificerad? | |
| Taby kommun | Lead, 300k | Workshop genomford 26 jan | | |
| TRS utokning | Kvalificerad, 1.5M | Mia saljer aktivt | Offert? | 1-3M? |
| SEB PWM | — | Johan uthyrd, 2.6M | Vunnen? | 2 600k? |
```

### Steg 6: Samla in svar

Stall fragorna fran sektion 2-4 som en konversation. En kund i taget eller grupperat — anpassa efter anvandarens preferens.

Acceptera muntliga svar. Anvandaren ska inte behova skriva exakta siffror — "ja kor", "nej, sank till 200", "vet inte, kolla med Emilia nasta vecka" ar alla giltiga svar.

Spara beslut med tidsstampel:

- Bekraftad budget -> skriv in
- Justerad budget -> skriv nytt varde
- Okand -> markera "TBD — kolla med [person]" och behall forra veckans varde (eller 0)

### Steg 7: Producera output

Producera tre filer:

**Fil 1: `drive-budget-{datum}.xlsx` — DigiClients-import**

Excel-fil med tre flikar:

Flik "Manadsdata":

| Kund | Jan 26 | Feb 26 | Mar 26 | ... | Dec 26 | Totalt |
|------|--------|--------|--------|-----|--------|--------|
| Budget: Clarendo | 0 | 250 000 | 250 000 | ... | 0 | 1 000 000 |
| Upsell: Clarendo | 0 | 0 | 0 | ... | 0 | 0 |
| Budget: AFRY | ... | | | | | |

Flik "Projektbudgetar":

| Kund | Projekt | Start | Slut | Feb Tim | Feb SEK | Mar Tim | Mar SEK | ... |
|------|---------|-------|------|---------|---------|---------|---------|-----|
| Clarendo | VERA 2026 | 2026-02-09 | 2026-05-29 | 50 | 250 000 | 50 | 250 000 | |

Flik "Pipeline":

| Kund | Board | Steg | Varde | Viktat varde | Kommentar |
|------|-------|------|-------|-------------|-----------|
| Besqab | Ovrigt salj | Kvalificerad | 500 000 | 125 000 | Workshops med IT-chef |

**Fil 2: `drive-budget-{datum}.md` — Markdown for MyMemory**

Strukturerad sammanfattning av alla bedomningar, beslut och oppna fragor.
Denna fil blir baseline for nasta veckas korning.

Format:

```markdown
# Drive Budget — {datum}

## Bedomningar gjorda

| Kund | Budget 2026 | Fordelning | Beslut | Kalla |
|------|------------|------------|--------|-------|
| Clarendo | 1 000 000 | 250k/man feb-maj | Accepterat | Harvest-budget + avtal |

## Pipeline

| Kund | Steg | Varde | Viktat | Andring |
|------|------|-------|--------|---------|
| Besqab | Kvalificerad | 500k | 125k | Uppgraderad fran Lead |

## Oppna fragor

| Kund | Fraga | Kolla med | Deadline |
|------|-------|-----------|----------|
| KIA | Projektkod och varde | Magnus | Nasta vecka |

## Ej andrade (bekraftade)

Alkoholhjalpen, AFRY, Industritorget, ...
```

**Fil 3: `drive-budget-log-{datum}.md` — Andringslogg**

Kort logg over vad som andrats sedan forra veckan. Avsedd for sparbarhet.

```markdown
# Andringslogg {datum}

- Clarendo: Budget satt 1M, 250k/man feb-maj (nytt — ej i DigiClients forut)
- Besqab: Pipeline Lead -> Kvalificerad, 500k
- TRS: Avvaktar — Mia kollar utokning
- KIA: Fortfarande okand — Magnus ej svarat
```

### Steg 8: Spara och presentera

1. Spara alla tre filer
2. Presentera Excel-filen till anvandaren (uppladdning i DigiClients)
3. Markdown-filerna sparas automatiskt -> MyMemory plockar upp dem som baseline till nasta vecka

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
| `harvest_get_project_tasks` | Lista tasks for ett projekt | `project_id` |
| `forecast_schedule` | Planerad allokering | `group_by`: "person" / "project" |

## Beroenden

| Beroende | Status | Fallback |
|----------|--------|----------|
| Harvest MCP | Kravs | Kan inte kora utan |
| Forecast MCP | Kravs | Kan inte kora utan |
| MyMemory MCP | Kravs (baseline + mjuk data) | Forsta korningen: alla kunder full bedomning |
| DigiClients-snapshot | Onskevart (visar nulage i DigiClients) | Kor utan — missar bara vad som redan fyllts i |
| drive-forecast skill | Anvands for steg 1-2 | Kan kora stegen direkt |

## Begransningar

- Skillen kan inte mata in data i DigiClients (webbformular, ingen API annu)
- Skillen kan inte kora Playwright-scriptet (kor lokalt pa anvandarens Mac)
- Pipeline-bedomningar kraver alltid mansklig input — ingen automatisk klassificering
- Timtaxor kan variera per person och projekt — verifiera mot Harvest-data, anta inte

## Utvecklingsplan

| Fas | Innehall | Status |
|-----|----------|--------|
| 1 | Manuell cykel: skill + konversation + Excel | Bygger nu |
| 2 | DigiClients-snapshot som indata (Playwright) | POC pagar |
| 3 | DigiClients filuppladdning/API for output | Vantar pa Ulrika |
| 4 | Schemalagd korning (cron + ToDo Container) | Framtida |
