"""
HarvestMCP - MCP-server för Harvest-rapporter.

Exponerar Harvest-data som MCP-verktyg för Claude Desktop, Cursor och andra AI-verktyg.
Fokus: Team utilization, tidsrapporter, projektöversikter.

CONTEXT-OPTIMERAD: Alla verktyg har max_rows och compact-läge för att
minimera token-förbrukning i LLM-kontextfönster.

Registrera i Claude Desktop:
{
    "mcpServers": {
        "harvest": {
            "command": "python3",
            "args": ["/Users/jekman/Projects/HarvestMCP/harvest_mcp.py"]
        }
    }
}
"""

import os
import sys
import signal
import logging
import uuid
from datetime import datetime, timedelta
from collections import defaultdict

# --- SIGTERM handler för graceful shutdown ---
def _handle_sigterm(signum, frame):
    os._exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

# --- LOGGING: Endast FileHandler, ingen terminal-output ---
# MCP-servrar använder stdout för protokoll
_log_dir = os.path.expanduser('~/.harvest/logs')
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, 'harvest_mcp.log')

_root = logging.getLogger()
_root.setLevel(logging.INFO)
for _h in _root.handlers[:]:
    _root.removeHandler(_h)

_fh = logging.FileHandler(_log_file)
_fh.setFormatter(logging.Formatter('%(asctime)s - HARVEST - %(levelname)s - %(message)s'))
_root.addHandler(_fh)

from mcp.server.fastmcp import FastMCP
from harvest_auth import load_config
from harvest_client import HarvestClient, ForecastClient

# Tysta tredjepartsloggers
for _name in ['httpx', 'httpcore', 'mcp', 'anyio', 'urllib3', 'requests']:
    logging.getLogger(_name).setLevel(logging.WARNING)

# --- CONFIG ---
try:
    CONFIG = load_config()
except FileNotFoundError as e:
    logging.error(f"Config load failed: {e}")
    CONFIG = {}

# --- MCP SERVER ---
_INSTRUCTIONS = """
Harvest- och Forecast-verktyg for tidsrapportering och resursplanering.

STRATEGI — minimera context-forbrukning:
1. Borja med harvest_team_utilization eller harvest_time_summary (group_by="summary") for oversikt.
2. Anvand filter (project_id, user_id, datumintervall) for att begränsa data.
3. For att hitta ID: anvand harvest_find_project("namn") eller harvest_find_user("namn") — INTE list-verktygen.
   - Soker default bara aktiva. Om inget hittas, prova active_only=false.
   - Anvand ALDRIG harvest_list_projects/harvest_list_users for ID-lookup — de returnerar tusentals rader.
4. Anropa harvest_detailed_time_entries BARA nar du behover enskilda poster eller entry_id.
5. Lat max_rows vara default (30). Oka bara om anvandaren explicit behover mer.
6. For tidrapportering: prepare -> granska -> commit. Alla poster via harvest_prepare_timesheet forst.
7. For team/roller: harvest_list_teams() for oversikt, harvest_get_team("namn") for medlemmar.
""".strip()

mcp = FastMCP("HarvestReports", instructions=_INSTRUCTIONS)

# Lazy-init clients
_client = None
_forecast_client = None

# --- Prepare/commit draft storage ---
_drafts = {}  # draft_id → draft-objekt
_DRAFT_TTL_MINUTES = 30

# --- Default limits ---
_DEFAULT_MAX_ROWS = 30


def _get_client() -> HarvestClient:
    """Lazy-initierad Harvest-klient."""
    global _client
    if _client is None:
        _client = HarvestClient(CONFIG['harvest'])
        logging.info("HarvestClient initialized")
    return _client


def _get_forecast_client() -> ForecastClient:
    """Lazy-initierad Forecast-klient."""
    global _forecast_client
    if _forecast_client is None:
        if 'forecast' not in CONFIG:
            raise RuntimeError("Forecast ej konfigurerad i config.yaml")
        _forecast_client = ForecastClient(CONFIG['forecast'])
        logging.info("ForecastClient initialized")
    return _forecast_client


def _cleanup_expired_drafts() -> None:
    """Rensa drafts äldre än TTL."""
    now = datetime.now()
    expired = [
        did for did, d in _drafts.items()
        if (now - d['created_at']).total_seconds() > _DRAFT_TTL_MINUTES * 60
    ]
    for did in expired:
        del _drafts[did]
        logging.info(f"Draft {did} expired, removed")


def _resolve_dates(from_date: str, to_date: str) -> tuple[str, str]:
    """Resolve tomma datum till aktuell vecka. Validerar format."""
    today = datetime.now().date()

    if not to_date:
        to_date = today.isoformat()

    if not from_date:
        monday = today - timedelta(days=today.weekday())
        from_date = monday.isoformat()

    for d, label in [(from_date, 'from_date'), (to_date, 'to_date')]:
        try:
            datetime.strptime(d, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Ogiltigt datumformat for {label}: '{d}'. Anvand YYYY-MM-DD.")

    return from_date, to_date


def _weeks_in_range(from_date: str, to_date: str) -> float:
    """Beräkna antal veckor i ett datumintervall."""
    d1 = datetime.strptime(from_date, '%Y-%m-%d').date()
    d2 = datetime.strptime(to_date, '%Y-%m-%d').date()
    days = (d2 - d1).days + 1
    return days / 7.0


def _truncation_note(shown: int, total: int) -> str:
    """Generera trunkerings-meddelande."""
    hidden = total - shown
    return f"\n*Visar {shown} av {total} rader. {hidden} dolda — oka max_rows for att se fler.*"


# ======================================================================
# TOOL 1: Team Utilization
# ======================================================================

@mcp.tool()
def harvest_team_utilization(
    from_date: str = "",
    to_date: str = "",
    max_rows: int = _DEFAULT_MAX_ROWS
) -> str:
    """
    Visa teamets belaggning och utilization for en period.

    Returnerar per person: totala timmar, billable/non-billable,
    kapacitet och utilization-procent.

    CONTEXT-TIPS: Returnerar alltid kompakt data (en rad per person).
    Bra som forsta steg for att fa en oversikt.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        max_rows: Max antal personer att visa (default 30, 0=alla)
    """
    from_date, to_date = _resolve_dates(from_date, to_date)
    client = _get_client()

    team_report = client.get_report_time_by_team(from_date, to_date)
    users = client.get_users(is_active=True)

    # user_id -> weekly_capacity (timmar)
    user_capacity = {}
    for u in users:
        uid = u['id']
        cap_seconds = u.get('weekly_capacity', 0) or 0
        user_capacity[uid] = cap_seconds / 3600.0

    weeks = _weeks_in_range(from_date, to_date)

    rows = []
    total_billable = 0.0
    total_nonbill = 0.0
    total_hours = 0.0
    total_capacity = 0.0

    for entry in team_report:
        uid = entry.get('user_id')
        name = entry.get('user_name', 'Unknown')
        billable_h = entry.get('billable_hours', 0) or 0
        total_h = entry.get('total_hours', 0) or 0
        nonbill_h = total_h - billable_h

        weekly_cap = user_capacity.get(uid, 40.0)
        period_cap = weekly_cap * weeks
        util_pct = (billable_h / period_cap * 100) if period_cap > 0 else 0

        rows.append({
            'name': name,
            'billable': billable_h,
            'nonbill': nonbill_h,
            'total': total_h,
            'capacity': period_cap,
            'util': util_pct,
        })

        total_billable += billable_h
        total_nonbill += nonbill_h
        total_hours += total_h
        total_capacity += period_cap

    rows.sort(key=lambda r: r['util'], reverse=True)
    total_count = len(rows)
    display = rows if (max_rows == 0) else rows[:max_rows]

    lines = [
        f"Team Utilization: {from_date} -> {to_date} ({weeks:.1f} veckor)\n",
        "| Person | Billable | Non-bill | Totalt | Kapacitet | Util% |",
        "|--------|----------|----------|--------|-----------|-------|",
    ]

    for r in display:
        lines.append(
            f"| {r['name']} | {r['billable']:.1f}h | {r['nonbill']:.1f}h | "
            f"{r['total']:.1f}h | {r['capacity']:.1f}h | {r['util']:.0f}% |"
        )

    avg_util = (total_billable / total_capacity * 100) if total_capacity > 0 else 0
    lines.append(
        f"| **Totalt** | **{total_billable:.1f}h** | **{total_nonbill:.1f}h** | "
        f"**{total_hours:.1f}h** | **{total_capacity:.1f}h** | **{avg_util:.0f}%** |"
    )

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(f"harvest_team_utilization: {from_date} -> {to_date}, {len(rows)} personer")
    return '\n'.join(lines)


# ======================================================================
# TOOL 2: Tidssammanstallning (merged time_summary + who_works_on_what)
# ======================================================================

@mcp.tool()
def harvest_time_summary(
    from_date: str = "",
    to_date: str = "",
    project_id: str = "",
    client_id: str = "",
    user_id: str = "",
    group_by: str = "summary",
    max_rows: int = _DEFAULT_MAX_ROWS
) -> str:
    """
    Flexibel tidssammanstallning — DET PRIMARA VERKTYGET for tidsoversikter.

    CONTEXT-TIPS FOR AI-AGENTER:
    - Borja ALLTID har for tidsfragor. Undvik harvest_detailed_time_entries
      om du inte verkligen behover enskilda poster.
    - Anvand group_by="summary" (default) for minst context-forbrukning.
    - Anvand filter (project_id, user_id) for att begränsa data.
    - Oka max_rows bara om du BEHOVER se fler rader.

    group_by-lagen:
    - "summary": Kompakt — en rad per projekt med totaler (STANDARD)
    - "project": Per projekt -> vilka personer jobbar dar
    - "person": Per person -> vilka projekt de jobbar med

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        project_id: Filtrera pa projekt-ID (valfritt)
        client_id: Filtrera pa kund-ID (valfritt)
        user_id: Filtrera pa person-ID (valfritt)
        group_by: "summary" (default), "project", eller "person"
        max_rows: Max antal rader i output (default 30, 0=alla)
    """
    from_date, to_date = _resolve_dates(from_date, to_date)
    client = _get_client()

    filters = {}
    if project_id:
        filters['project_id'] = project_id
    if client_id:
        filters['client_id'] = client_id
    if user_id:
        filters['user_id'] = user_id

    entries = client.get_time_entries(from_date, to_date, **filters)

    if not entries:
        return f"Inga tidsposter hittades for {from_date} -> {to_date} med angivna filter."

    if group_by == "person":
        result = _format_by_person(entries, from_date, to_date, max_rows)
    elif group_by == "project":
        result = _format_by_project(entries, from_date, to_date, max_rows)
    else:
        result = _format_summary(entries, from_date, to_date, max_rows)

    logging.info(
        f"harvest_time_summary({group_by}): {from_date} -> {to_date}, "
        f"{len(entries)} entries, max_rows={max_rows}"
    )
    return result


def _format_summary(entries: list, from_date: str, to_date: str, max_rows: int) -> str:
    """Kompakt sammanfattning — en rad per projekt."""
    total_hours = 0.0
    billable_hours = 0.0
    billable_amount = 0.0
    by_project = defaultdict(lambda: {'hours': 0.0, 'billable_hours': 0.0, 'persons': set()})

    for e in entries:
        hours = e.get('hours', 0) or 0
        total_hours += hours

        if e.get('billable', False):
            billable_hours += hours
            rate = e.get('billable_rate') or 0
            billable_amount += hours * rate

        proj_name = (e.get('project', {}) or {}).get('name', 'Unknown')
        person = (e.get('user', {}) or {}).get('name', 'Unknown')

        by_project[proj_name]['hours'] += hours
        if e.get('billable', False):
            by_project[proj_name]['billable_hours'] += hours
        by_project[proj_name]['persons'].add(person)

    nonbill_hours = total_hours - billable_hours

    lines = [
        f"Tidssammanstallning: {from_date} -> {to_date} | "
        f"Totalt: {total_hours:.1f}h | Billable: {billable_hours:.1f}h | "
        f"Non-billable: {nonbill_hours:.1f}h",
    ]

    if billable_amount > 0:
        lines[0] += f" | Belopp: {billable_amount:,.0f} SEK"

    lines.append("")
    lines.append("| Projekt | Timmar | Billable | Pers |")
    lines.append("|---------|--------|----------|------|")

    sorted_projects = sorted(by_project.items(), key=lambda x: x[1]['hours'], reverse=True)
    total_count = len(sorted_projects)
    display = sorted_projects if (max_rows == 0) else sorted_projects[:max_rows]

    for proj_name, data in display:
        num_persons = len(data['persons'])
        lines.append(
            f"| {proj_name} | {data['hours']:.1f}h | {data['billable_hours']:.1f}h | {num_persons} |"
        )

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    return '\n'.join(lines)


def _format_by_project(entries: list, from_date: str, to_date: str, max_rows: int) -> str:
    """Gruppera tidsposter per projekt -> per person."""
    projects = defaultdict(lambda: {
        'client_name': '',
        'persons': defaultdict(lambda: {'hours': 0.0, 'billable_hours': 0.0}),
        'total_hours': 0.0,
    })

    for e in entries:
        proj_name = (e.get('project', {}) or {}).get('name', 'Unknown')
        client_name = (e.get('client', {}) or {}).get('name', '')
        person = (e.get('user', {}) or {}).get('name', 'Unknown')
        hours = e.get('hours', 0) or 0
        billable = e.get('billable', False)

        projects[proj_name]['client_name'] = client_name
        projects[proj_name]['persons'][person]['hours'] += hours
        if billable:
            projects[proj_name]['persons'][person]['billable_hours'] += hours
        projects[proj_name]['total_hours'] += hours

    sorted_projects = sorted(projects.items(), key=lambda x: x[1]['total_hours'], reverse=True)
    total_count = len(sorted_projects)
    display = sorted_projects if (max_rows == 0) else sorted_projects[:max_rows]

    lines = [f"Per projekt: {from_date} -> {to_date}\n"]

    row_count = 0
    for proj_name, data in display:
        client_info = f" ({data['client_name']})" if data['client_name'] else ""
        lines.append(f"**{proj_name}{client_info}** — {data['total_hours']:.1f}h")

        sorted_persons = sorted(
            data['persons'].items(), key=lambda x: x[1]['hours'], reverse=True
        )
        parts = []
        for person, pdata in sorted_persons:
            bill_str = f" (bill: {pdata['billable_hours']:.1f}h)" if pdata['billable_hours'] > 0 else ""
            parts.append(f"  {person}: {pdata['hours']:.1f}h{bill_str}")
        lines.extend(parts)
        lines.append("")
        row_count += 1

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    return '\n'.join(lines)


def _format_by_person(entries: list, from_date: str, to_date: str, max_rows: int) -> str:
    """Gruppera tidsposter per person -> per projekt."""
    persons = defaultdict(lambda: {
        'projects': defaultdict(lambda: {'hours': 0.0, 'client_name': ''}),
        'total_hours': 0.0,
    })

    for e in entries:
        proj_name = (e.get('project', {}) or {}).get('name', 'Unknown')
        client_name = (e.get('client', {}) or {}).get('name', '')
        person = (e.get('user', {}) or {}).get('name', 'Unknown')
        hours = e.get('hours', 0) or 0

        persons[person]['projects'][proj_name]['hours'] += hours
        persons[person]['projects'][proj_name]['client_name'] = client_name
        persons[person]['total_hours'] += hours

    sorted_persons = sorted(persons.items(), key=lambda x: x[1]['total_hours'], reverse=True)
    total_count = len(sorted_persons)
    display = sorted_persons if (max_rows == 0) else sorted_persons[:max_rows]

    lines = [f"Per person: {from_date} -> {to_date}\n"]

    for person, data in display:
        lines.append(f"**{person}** — {data['total_hours']:.1f}h")
        sorted_projects = sorted(
            data['projects'].items(), key=lambda x: x[1]['hours'], reverse=True
        )
        for proj_name, pdata in sorted_projects:
            lines.append(f"  {proj_name} ({pdata['client_name']}): {pdata['hours']:.1f}h")
        lines.append("")

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    return '\n'.join(lines)


# ======================================================================
# TOOL 3: Detaljerade tidsposter (med kommentarer)
# ======================================================================

@mcp.tool()
def harvest_detailed_time_entries(
    from_date: str = "",
    to_date: str = "",
    project_id: str = "",
    client_id: str = "",
    user_id: str = "",
    max_rows: int = _DEFAULT_MAX_ROWS
) -> str:
    """
    Visa enskilda tidsposter med kommentarer/notes och entry_id.

    CONTEXT-TIPS FOR AI-AGENTER:
    - DYRT verktyg — varje tidspost = en rad. Anvand harvest_time_summary
      forst for att fa oversikt, och detta verktyg bara nar du behover:
      1. Granska enskilda kommentarer
      2. Hitta entry_id for update/delete
      3. Kontrollera poster utan kommentar
    - Anvand ALLTID filter (user_id, project_id) for att begränsa.
    - max_rows=30 ar default. Oka bara vid behov.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        project_id: Filtrera pa projekt-ID (valfritt)
        client_id: Filtrera pa kund-ID (valfritt)
        user_id: Filtrera pa person-ID (valfritt)
        max_rows: Max antal rader (default 30, 0=alla)
    """
    from_date, to_date = _resolve_dates(from_date, to_date)
    client = _get_client()

    filters = {}
    if project_id:
        filters['project_id'] = project_id
    if client_id:
        filters['client_id'] = client_id
    if user_id:
        filters['user_id'] = user_id

    entries = client.get_time_entries(from_date, to_date, **filters)

    if not entries:
        return f"Inga tidsposter hittades for {from_date} -> {to_date} med angivna filter."

    # Sortera: nyast datum forst, sedan person
    entries.sort(key=lambda e: (
        e.get('spent_date', ''),
        (e.get('user', {}) or {}).get('name', '')
    ), reverse=True)

    total_count = len(entries)
    total_hours = sum(e.get('hours', 0) or 0 for e in entries)
    missing_notes = sum(1 for e in entries if not (e.get('notes') or '').strip())

    display = entries if (max_rows == 0) else entries[:max_rows]

    lines = [
        f"Tidsposter: {from_date} -> {to_date} | "
        f"{total_count} poster | {total_hours:.1f}h | {missing_notes} utan kommentar\n",
        "| ID | Datum | Person | Projekt | Task | Tim | Kommentar |",
        "|----|-------|--------|---------|------|-----|-----------|",
    ]

    for e in display:
        eid = e.get('id', '')
        date = e.get('spent_date', '')
        person = (e.get('user', {}) or {}).get('name', 'Unknown')
        proj_name = (e.get('project', {}) or {}).get('name', 'Unknown')
        task_name = (e.get('task', {}) or {}).get('name', '')
        hours = e.get('hours', 0) or 0
        notes = (e.get('notes') or '').strip()

        if len(notes) > 60:
            notes = notes[:57] + '...'
        notes = notes.replace('|', '\\|')

        lines.append(
            f"| {eid} | {date} | {person} | {proj_name} | {task_name} | {hours:.1f} | {notes} |"
        )

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(
        f"harvest_detailed_time_entries: {from_date} -> {to_date}, "
        f"{total_count} entries, showing {len(display)}, {missing_notes} utan notes"
    )
    return '\n'.join(lines)


# ======================================================================
# TOOL 4a: Fuzzy-sok projekt (BILLIGT — anvand detta for ID-lookup)
# ======================================================================

def _fuzzy_match(query: str, text: str) -> bool:
    """Case-insensitive substring-matchning."""
    return query.lower() in text.lower()


@mcp.tool()
def harvest_find_project(query: str, active_only: bool = True) -> str:
    """
    Sok projekt pa namn (fuzzy). Returnerar matchande projekt med ID.

    CONTEXT-TIPS: Anvand DETTA istallet for harvest_list_projects nar du
    behover hitta ett projekt-ID. Returnerar bara matchande rader.
    Exempel: harvest_find_project("besqab") -> ID, namn, kund.

    Om sokningen inte ger resultat med aktiva projekt, prova active_only=false
    for att inkludera avslutade/inaktiva projekt.

    Args:
        query: Sokord (matchar delstrang i projektnamn eller kundnamn, case-insensitive)
        active_only: Bara aktiva projekt (default: True). Satt till False for att soka bland alla.
    """
    client = _get_client()
    projects = client.get_projects(is_active=active_only)

    matches = []
    for p in projects:
        proj_name = p.get('name', '')
        client_name = (p.get('client') or {}).get('name', '')
        if _fuzzy_match(query, proj_name) or _fuzzy_match(query, client_name):
            matches.append(p)

    if not matches:
        hint = " Prova active_only=false for att inkludera inaktiva projekt." if active_only else ""
        return f"Inga projekt matchade '{query}'.{hint}"

    lines = []
    for p in sorted(matches, key=lambda x: x.get('name', '')):
        client_name = (p.get('client') or {}).get('name', '')
        active_marker = "" if active_only else (" [aktiv]" if p.get('is_active') else " [inaktiv]")
        lines.append(f"{p['id']} | {p['name']} | {client_name}{active_marker}")

    logging.info(f"harvest_find_project: query='{query}', active_only={active_only}, {len(matches)} matchningar")
    return '\n'.join(lines)


# ======================================================================
# TOOL 4b: Fuzzy-sok anvandare (BILLIGT — anvand detta for ID-lookup)
# ======================================================================

@mcp.tool()
def harvest_find_user(query: str, active_only: bool = True) -> str:
    """
    Sok anvandare pa namn (fuzzy). Returnerar matchande anvandare med ID.

    CONTEXT-TIPS: Anvand DETTA istallet for harvest_list_users nar du
    behover hitta ett user-ID. Returnerar bara matchande rader.
    Exempel: harvest_find_user("anna") -> ID, namn, kapacitet.

    Om sokningen inte ger resultat med aktiva anvandare, prova active_only=false.

    Args:
        query: Sokord (matchar delstrang i for- eller efternamn, case-insensitive)
        active_only: Bara aktiva anvandare (default: True). Satt till False for att soka bland alla.
    """
    client = _get_client()
    users = client.get_users(is_active=active_only)

    matches = []
    for u in users:
        full_name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
        if _fuzzy_match(query, full_name):
            matches.append(u)

    if not matches:
        hint = " Prova active_only=false for att inkludera inaktiva anvandare." if active_only else ""
        return f"Inga anvandare matchade '{query}'.{hint}"

    lines = []
    for u in sorted(matches, key=lambda x: x.get('first_name', '')):
        name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
        cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
        active_marker = "" if active_only else (" [aktiv]" if u.get('is_active') else " [inaktiv]")
        lines.append(f"{u['id']} | {name} | {cap_h:.0f}h/v{active_marker}")

    logging.info(f"harvest_find_user: query='{query}', active_only={active_only}, {len(matches)} matchningar")
    return '\n'.join(lines)


# ======================================================================
# TOOL 4c: Lista alla projekt (DYRT — anvand harvest_find_project forst)
# ======================================================================

@mcp.tool()
def harvest_list_projects(active_only: bool = True, max_rows: int = 0) -> str:
    """
    Lista ALLA Harvest-projekt. DYRT — returnerar hela listan.

    CONTEXT-TIPS: Anvand harvest_find_project(query) istallet om du soker
    ett specifikt projekt. Anvand bara detta for att visa hela listan.

    Args:
        active_only: Visa bara aktiva projekt (default: True)
        max_rows: Max antal rader (default 0=alla)
    """
    client = _get_client()
    projects = client.get_projects(is_active=active_only)

    if not projects:
        return "Inga projekt hittades."

    sorted_projects = sorted(projects, key=lambda x: x.get('name', ''))
    total_count = len(sorted_projects)
    display = sorted_projects if (max_rows == 0) else sorted_projects[:max_rows]

    lines = [
        f"Projekt {'(aktiva)' if active_only else '(alla)'}: {total_count} st\n",
        "| ID | Projekt | Kund | Billable |",
        "|----|---------|------|----------|",
    ]

    for p in display:
        client_obj = p.get('client')
        client_name = client_obj['name'] if client_obj else '\u2014'
        billable = 'Ja' if p.get('is_billable') else 'Nej'
        lines.append(f"| {p['id']} | {p['name']} | {client_name} | {billable} |")

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(f"harvest_list_projects: {total_count} projekt, showing {len(display)}")
    return '\n'.join(lines)


# ======================================================================
# TOOL 5: Lista användare (hjälpverktyg)
# ======================================================================

@mcp.tool()
def harvest_list_users(active_only: bool = True, max_rows: int = 0) -> str:
    """
    Lista ALLA Harvest-anvandare. DYRT — returnerar hela listan.

    CONTEXT-TIPS: Anvand harvest_find_user(query) istallet om du soker
    en specifik person. Anvand bara detta for att visa hela listan.

    Args:
        active_only: Visa bara aktiva anvandare (default: True)
        max_rows: Max antal rader (default 0=alla)
    """
    client = _get_client()
    users = client.get_users(is_active=active_only)

    if not users:
        return "Inga anvandare hittades."

    sorted_users = sorted(users, key=lambda x: x.get('first_name', ''))
    total_count = len(sorted_users)
    display = sorted_users if (max_rows == 0) else sorted_users[:max_rows]

    lines = [
        f"Anvandare {'(aktiva)' if active_only else '(alla)'}: {total_count} st\n",
        "| ID | Namn | Kapacitet |",
        "|----|------|-----------|",
    ]

    for u in display:
        name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
        cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
        lines.append(f"| {u['id']} | {name} | {cap_h:.0f}h/v |")

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(f"harvest_list_users: {total_count} anvandare, showing {len(display)}")
    return '\n'.join(lines)


# ======================================================================
# TOOL 6: Forecast - Vem är schemalagd var
# ======================================================================

@mcp.tool()
def forecast_schedule(
    start_date: str = "",
    end_date: str = "",
    group_by: str = "person",
    max_rows: int = _DEFAULT_MAX_ROWS
) -> str:
    """
    Visa vem som ar schemalagd pa vilka projekt i Forecast.

    Visar planerad allokering (timmar) for varje person och projekt.

    CONTEXT-TIPS: Returnerar kompakt output (inga tabeller, bara listor).

    Args:
        start_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        end_date: Slutdatum YYYY-MM-DD (default: fredagen denna vecka)
        group_by: "person" (vilka projekt per person) eller "project" (vilka personer per projekt)
        max_rows: Max antal rader i output (default 30, 0=alla)
    """
    today = datetime.now().date()
    if not start_date:
        monday = today - timedelta(days=today.weekday())
        start_date = monday.isoformat()
    if not end_date:
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)
        end_date = friday.isoformat()

    fc = _get_forecast_client()

    assignments = fc.get_assignments(start_date, end_date)
    people = fc.get_people()
    projects = fc.get_projects()

    people_map = {p['id']: p for p in people}
    project_map = {p['id']: p for p in projects}

    if group_by == "project":
        return _format_forecast_by_project(
            assignments, people_map, project_map, start_date, end_date, max_rows
        )
    else:
        return _format_forecast_by_person(
            assignments, people_map, project_map, start_date, end_date, max_rows
        )


def _format_forecast_by_person(
    assignments: list, people_map: dict, project_map: dict,
    start_date: str, end_date: str, max_rows: int = 0
) -> str:
    """Gruppera Forecast-assignments per person — kompakt format."""
    persons = defaultdict(lambda: {'projects': defaultdict(float), 'total': 0.0})

    for a in assignments:
        person_id = a.get('person_id')
        project_id = a.get('project_id')
        if not person_id or not project_id:
            continue

        person = people_map.get(person_id)
        project = project_map.get(project_id)
        if not person or not project:
            continue

        person_name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        project_name = project.get('name', 'Unknown')

        alloc_seconds = a.get('allocation', 0) or 0
        alloc_hours_per_day = alloc_seconds / 3600.0

        a_start = max(a.get('start_date', start_date), start_date)
        a_end = min(a.get('end_date', end_date), end_date)
        work_days = _count_work_days(a_start, a_end)
        total_hours = alloc_hours_per_day * work_days

        persons[person_name]['projects'][project_name] += total_hours
        persons[person_name]['total'] += total_hours

    sorted_persons = sorted(persons.items(), key=lambda x: x[1]['total'], reverse=True)
    total_count = len(sorted_persons)
    display = sorted_persons if (max_rows == 0) else sorted_persons[:max_rows]

    lines = [f"Forecast: {start_date} -> {end_date}\n"]

    for person_name, data in display:
        projs = ', '.join(
            f"{proj} {hours:.1f}h"
            for proj, hours in sorted(data['projects'].items(), key=lambda x: x[1], reverse=True)
        )
        lines.append(f"**{person_name}** ({data['total']:.1f}h): {projs}")

    if not sorted_persons:
        lines.append("Inga assignments hittades for perioden.")

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(f"forecast_schedule(person): {start_date} -> {end_date}, {len(sorted_persons)} personer")
    return '\n'.join(lines)


def _format_forecast_by_project(
    assignments: list, people_map: dict, project_map: dict,
    start_date: str, end_date: str, max_rows: int = 0
) -> str:
    """Gruppera Forecast-assignments per projekt — kompakt format."""
    projects = defaultdict(lambda: {'persons': defaultdict(float), 'total': 0.0})

    for a in assignments:
        person_id = a.get('person_id')
        project_id = a.get('project_id')
        if not person_id or not project_id:
            continue

        person = people_map.get(person_id)
        project = project_map.get(project_id)
        if not person or not project:
            continue

        person_name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        project_name = project.get('name', 'Unknown')

        alloc_seconds = a.get('allocation', 0) or 0
        alloc_hours_per_day = alloc_seconds / 3600.0

        a_start = max(a.get('start_date', start_date), start_date)
        a_end = min(a.get('end_date', end_date), end_date)
        work_days = _count_work_days(a_start, a_end)
        total_hours = alloc_hours_per_day * work_days

        projects[project_name]['persons'][person_name] += total_hours
        projects[project_name]['total'] += total_hours

    sorted_projects = sorted(projects.items(), key=lambda x: x[1]['total'], reverse=True)
    total_count = len(sorted_projects)
    display = sorted_projects if (max_rows == 0) else sorted_projects[:max_rows]

    lines = [f"Forecast per projekt: {start_date} -> {end_date}\n"]

    for proj_name, data in display:
        persons = ', '.join(
            f"{person} {hours:.1f}h"
            for person, hours in sorted(data['persons'].items(), key=lambda x: x[1], reverse=True)
        )
        lines.append(f"**{proj_name}** ({data['total']:.1f}h): {persons}")

    if not sorted_projects:
        lines.append("Inga assignments hittades for perioden.")

    if max_rows and total_count > max_rows:
        lines.append(_truncation_note(max_rows, total_count))

    logging.info(f"forecast_schedule(project): {start_date} -> {end_date}, {len(sorted_projects)} projekt")
    return '\n'.join(lines)


def _count_work_days(start_date: str, end_date: str) -> int:
    """Räkna arbetsdagar (mån-fre) i ett intervall."""
    d1 = datetime.strptime(start_date, '%Y-%m-%d').date()
    d2 = datetime.strptime(end_date, '%Y-%m-%d').date()
    count = 0
    current = d1
    while current <= d2:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


# ======================================================================
# TOOL 7: Lista tasks för ett projekt
# ======================================================================

@mcp.tool()
def harvest_get_project_tasks(project_id: int) -> str:
    """
    Lista tillgangliga tasks for ett Harvest-projekt.

    Returnerar task-ID och namn. Behovs for att valja ratt task_id
    vid skapande av tidsposter.

    Args:
        project_id: Harvest projekt-ID (hamta fran harvest_list_projects)
    """
    client = _get_client()
    assignments = client.get_task_assignments(project_id)

    if not assignments:
        return f"Inga tasks hittades for projekt {project_id}."

    lines = [
        f"Tasks for projekt {project_id}:\n",
        "| Task ID | Namn | Billable |",
        "|---------|------|----------|",
    ]

    for a in assignments:
        task = a.get('task', {}) or {}
        task_id = task.get('id', '\u2014')
        task_name = task.get('name', 'Unknown')
        billable = 'Ja' if a.get('billable') else 'Nej'
        lines.append(f"| {task_id} | {task_name} | {billable} |")

    logging.info(f"harvest_get_project_tasks: projekt {project_id}, {len(assignments)} tasks")
    return '\n'.join(lines)


# ======================================================================
# TOOL 8: Lista team/roller
# ======================================================================

@mcp.tool()
def harvest_list_teams() -> str:
    """
    Lista alla team (roller) i Harvest med antal medlemmar.

    Harvest-roller motsvarar team. Returnerar kompakt oversikt:
    roll-ID, namn och antal personer.

    CONTEXT-TIPS: Billigt verktyg — en rad per roll.
    Anvand harvest_get_team(query) for att se medlemmarna i ett specifikt team.
    """
    client = _get_client()
    roles = client.get_roles()

    if not roles:
        return "Inga roller/team hittades."

    sorted_roles = sorted(roles, key=lambda r: r.get('name', ''))

    lines = [
        f"Team/roller: {len(sorted_roles)} st\n",
        "| ID | Team | Medlemmar |",
        "|----|------|-----------|",
    ]

    for r in sorted_roles:
        count = len(r.get('user_ids', []))
        lines.append(f"| {r['id']} | {r['name']} | {count} |")

    logging.info(f"harvest_list_teams: {len(sorted_roles)} roller")
    return '\n'.join(lines)


# ======================================================================
# TOOL 9: Visa teammedlemmar
# ======================================================================

@mcp.tool()
def harvest_get_team(query: str) -> str:
    """
    Sok team pa namn (fuzzy) och visa dess medlemmar.

    Returnerar teamnamn och alla medlemmar med ID, namn och kapacitet.

    CONTEXT-TIPS: Anvand harvest_list_teams() forst for att se alla team,
    sedan detta verktyg for att se medlemmarna i ett specifikt team.

    Args:
        query: Sokord (matchar delstrang i rollnamn, case-insensitive)
    """
    client = _get_client()
    roles = client.get_roles()

    matches = [r for r in roles if _fuzzy_match(query, r.get('name', ''))]

    if not matches:
        return f"Inget team matchade '{query}'. Anvand harvest_list_teams() for att se alla."

    # Hämta alla aktiva + inaktiva users för att kunna resolve:a user_ids
    users = client.get_users(is_active=True)
    inactive_users = client.get_users(is_active=False)
    user_map = {}
    for u in users + inactive_users:
        user_map[u['id']] = u

    lines = []
    for role in matches:
        member_ids = role.get('user_ids', [])
        lines.append(f"**{role['name']}** (id={role['id']}, {len(member_ids)} medlemmar)\n")
        lines.append("| ID | Namn | Kapacitet | Aktiv |")
        lines.append("|----|------|-----------|-------|")

        for uid in sorted(member_ids):
            u = user_map.get(uid)
            if u:
                name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
                cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
                active = "Ja" if u.get('is_active') else "Nej"
                lines.append(f"| {uid} | {name} | {cap_h:.0f}h/v | {active} |")
            else:
                lines.append(f"| {uid} | (okand) | — | — |")

        lines.append("")

    logging.info(f"harvest_get_team: query='{query}', {len(matches)} matchningar")
    return '\n'.join(lines)


# ======================================================================
# TOOL 10: Prepare timesheet (draft)
# ======================================================================

@mcp.tool()
def harvest_prepare_timesheet(entries: str, user_id: int = 0) -> str:
    """
    Skapa ett utkast (draft) av tidsposter for granskning innan commit.

    ALL skapning av tidsposter MASTE ga via prepare -> commit.
    Returnerar en preview-tabell och ett draft_id som anvands i commit.

    Args:
        entries: JSON-lista med entries. Varje entry: {"project_id": int, "task_id": int, "spent_date": "YYYY-MM-DD", "hours": float, "notes": "beskrivning"}
        user_id: Harvest user-ID (0 = inloggad anvandare)
    """
    import json as _json

    _cleanup_expired_drafts()

    try:
        entry_list = _json.loads(entries)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Ogiltigt JSON i entries: {e}")

    if not isinstance(entry_list, list) or len(entry_list) == 0:
        raise ValueError("entries maste vara en icke-tom JSON-lista.")

    client = _get_client()

    # Validera alla entries först (snabb loop, inga API-anrop)
    for i, entry in enumerate(entry_list):
        for field in ('project_id', 'task_id', 'spent_date', 'hours'):
            if field not in entry:
                raise ValueError(f"Entry {i}: saknar obligatoriskt falt '{field}'")
        try:
            datetime.strptime(entry['spent_date'], '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Entry {i}: ogiltigt datumformat '{entry['spent_date']}'. Anvand YYYY-MM-DD.")
        if not isinstance(entry['hours'], (int, float)) or entry['hours'] <= 0:
            raise ValueError(f"Entry {i}: hours maste vara > 0, fick {entry['hours']}")

    # Hämta projektnamn — EN gång, bara namn
    project_cache = {}
    try:
        projects = client.get_projects(is_active=True)
        for p in projects:
            project_cache[p['id']] = p['name']
    except Exception:
        pass

    # Hämta tasknamn — en gång per unikt projekt
    task_cache = {}
    unique_pids = set(e['project_id'] for e in entry_list)
    for pid in unique_pids:
        try:
            assignments = client.get_task_assignments(pid)
            for a in assignments:
                t = a.get('task', {}) or {}
                task_cache[(pid, t.get('id'))] = t.get('name', str(t.get('id')))
        except Exception:
            pass

    # Bygg validerad lista med namn
    validated = []
    for i, entry in enumerate(entry_list):
        pid = entry['project_id']
        tid = entry['task_id']
        notes = (entry.get('notes') or '').strip()

        validated.append({
            'project_id': pid,
            'task_id': tid,
            'spent_date': entry['spent_date'],
            'hours': entry['hours'],
            'notes': notes,
            'project_name': project_cache.get(pid, str(pid)),
            'task_name': task_cache.get((pid, tid), str(tid)),
        })

    # Skapa draft
    draft_id = str(uuid.uuid4())[:8]
    _drafts[draft_id] = {
        'entries': validated,
        'user_id': user_id if user_id else None,
        'created_at': datetime.now(),
        'committed': False,
    }

    # Bygg preview
    total_hours = sum(e['hours'] for e in validated)
    lines = [
        f"Draft: {draft_id} | {len(validated)} poster | {total_hours:.1f}h\n",
        "| # | Datum | Projekt | Task | Tim | Notes |",
        "|---|-------|---------|------|-----|-------|",
    ]
    for i, e in enumerate(validated, 1):
        notes_preview = e['notes'][:50] + '...' if len(e['notes']) > 50 else e['notes']
        notes_preview = notes_preview.replace('|', '\\|')
        lines.append(
            f"| {i} | {e['spent_date']} | {e['project_name']} | {e['task_name']} | "
            f"{e['hours']:.1f} | {notes_preview} |"
        )

    lines.append(f"\ndraft_id: `{draft_id}` — giltig {_DRAFT_TTL_MINUTES} min. Anropa harvest_commit_timesheet(draft_id) for att posta.")

    logging.info(f"harvest_prepare_timesheet: draft {draft_id}, {len(validated)} entries, {total_hours:.1f}h")
    return '\n'.join(lines)


# ======================================================================
# TOOL 11: Commit timesheet (draft -> Harvest)
# ======================================================================

@mcp.tool()
def harvest_commit_timesheet(draft_id: str) -> str:
    """
    Posta ett tidigare forberett utkast till Harvest.

    HARDFAIL om draft saknas, har expired eller redan ar committed.
    Postar entries i ordning. Vid fel STOPPAS processen och
    rapporterar vilken rad som felade.

    Args:
        draft_id: Draft-ID fran harvest_prepare_timesheet
    """
    if draft_id not in _drafts:
        raise ValueError(f"Draft '{draft_id}' finns inte eller har expired.")

    draft = _drafts[draft_id]

    if draft['committed']:
        raise ValueError(f"Draft '{draft_id}' har redan committats.")

    # Kolla TTL
    age_minutes = (datetime.now() - draft['created_at']).total_seconds() / 60
    if age_minutes > _DRAFT_TTL_MINUTES:
        del _drafts[draft_id]
        raise ValueError(f"Draft '{draft_id}' har expired ({age_minutes:.0f} min > {_DRAFT_TTL_MINUTES} min).")

    client = _get_client()
    uid = draft['user_id']
    results = []

    for i, entry in enumerate(draft['entries']):
        try:
            result = client.create_time_entry(
                project_id=entry['project_id'],
                task_id=entry['task_id'],
                spent_date=entry['spent_date'],
                hours=entry['hours'],
                notes=entry['notes'],
                user_id=uid,
            )
            entry_id = result.get('id', '?')
            results.append(f"| {i+1} | {entry['spent_date']} | {entry['project_name']} | {entry['hours']:.1f}h | {entry_id} |")
        except Exception as e:
            draft['committed'] = True
            lines = [
                f"Commit AVBRUTEN vid rad {i+1} av {len(draft['entries'])}",
                f"Fel: {e}\n",
            ]
            if results:
                lines.append("Lyckade poster:")
                lines.append("| # | Datum | Projekt | Tim | Entry ID |")
                lines.append("|---|-------|---------|-----|----------|")
                lines.extend(results)
            lines.append(f"\n{len(draft['entries']) - i} poster EJ postade. Korrigera och skapa ny draft.")
            logging.error(f"harvest_commit_timesheet: draft {draft_id} failed at entry {i}: {e}")
            return '\n'.join(lines)

    draft['committed'] = True

    lines = [
        f"Commit klar: {draft_id} | {len(results)} poster postade\n",
        "| # | Datum | Projekt | Tim | Entry ID |",
        "|---|-------|---------|-----|----------|",
    ]
    lines.extend(results)

    logging.info(f"harvest_commit_timesheet: draft {draft_id}, {len(results)} entries committed")
    return '\n'.join(lines)


# ======================================================================
# TOOL 12: Uppdatera tidspost
# ======================================================================

@mcp.tool()
def harvest_update_time_entry(
    entry_id: int,
    hours: float = 0.0,
    notes: str = "",
    project_id: int = 0,
    task_id: int = 0
) -> str:
    """
    Uppdatera en befintlig tidspost i Harvest.

    Ange bara de falt som ska andras. Oandrade falt behalls.

    Args:
        entry_id: Tidspostens ID (fran harvest_detailed_time_entries)
        hours: Nya timmar (0 = andras inte)
        notes: Ny kommentar (tom = andras inte)
        project_id: Nytt projekt-ID (0 = andras inte)
        task_id: Nytt task-ID (0 = andras inte)
    """
    client = _get_client()

    fields = {}
    if hours:
        fields['hours'] = hours
    if notes:
        fields['notes'] = notes
    if project_id:
        fields['project_id'] = project_id
    if task_id:
        fields['task_id'] = task_id

    if not fields:
        return "Inga falt att uppdatera. Ange minst hours, notes, project_id eller task_id."

    result = client.update_time_entry(entry_id, **fields)

    proj_name = (result.get('project', {}) or {}).get('name', '?')
    updated_hours = result.get('hours', '?')

    logging.info(f"harvest_update_time_entry: entry {entry_id} -> {fields}")
    return (
        f"Uppdaterad: entry_id={entry_id} | {proj_name} | "
        f"{updated_hours}h | falt: {', '.join(fields.keys())}"
    )


# ======================================================================
# TOOL 13: Ta bort tidspost
# ======================================================================

@mcp.tool()
def harvest_delete_time_entry(entry_id: int) -> str:
    """
    Ta bort en tidspost fran Harvest.

    VARNING: Denna operation kan inte angras.

    Args:
        entry_id: Tidspostens ID (fran harvest_detailed_time_entries)
    """
    client = _get_client()
    client.delete_time_entry(entry_id)

    logging.info(f"harvest_delete_time_entry: entry {entry_id} borttagen")
    return f"Borttagen: entry_id={entry_id}"


# ======================================================================
# TOOL 14: Self-update från GitHub
# ======================================================================

# Projektrot (där detta repo bor)
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(cmd: list[str], cwd: str = _PROJECT_DIR) -> tuple[int, str]:
    """Kör subprocess, returnera (returncode, combined output)."""
    import subprocess
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=120
    )
    return result.returncode, (result.stdout + result.stderr).strip()


@mcp.tool()
def harvest_self_update() -> str:
    """
    Uppdatera HarvestMCP fran GitHub.

    Kollar om det finns nya commits pa origin/main, pullar andringar,
    och uppdaterar Python-dependencies om requirements.txt andrats.

    Servern behover startas om efter uppdatering (stang och oppna Claude Desktop).
    """
    import hashlib

    lines = ["HarvestMCP Self-Update\n"]

    rc, out = _run(['git', 'rev-parse', '--is-inside-work-tree'])
    if rc != 0:
        raise RuntimeError(f"Inte ett git-repo: {_PROJECT_DIR}")

    rc, out = _run(['git', 'fetch', 'origin', 'main'])
    if rc != 0:
        raise RuntimeError(f"git fetch misslyckades: {out}")

    rc, local_sha = _run(['git', 'rev-parse', 'HEAD'])
    rc2, remote_sha = _run(['git', 'rev-parse', 'origin/main'])
    if rc != 0 or rc2 != 0:
        raise RuntimeError("Kunde inte lasa git SHA")

    local_sha = local_sha.strip()
    remote_sha = remote_sha.strip()

    if local_sha == remote_sha:
        lines.append(f"Redan uppdaterad. Commit: {local_sha[:8]}")
        logging.info("harvest_self_update: already up to date")
        return '\n'.join(lines)

    rc, log_output = _run([
        'git', 'log', '--oneline', f'{local_sha}..{remote_sha}'
    ])
    commit_count = len(log_output.strip().split('\n')) if log_output.strip() else 0
    lines.append(f"{commit_count} nya commits:\n{log_output}\n")

    req_path = os.path.join(_PROJECT_DIR, 'requirements.txt')
    old_req_hash = ""
    if os.path.exists(req_path):
        with open(req_path, 'rb') as f:
            old_req_hash = hashlib.sha256(f.read()).hexdigest()

    rc, out = _run(['git', 'pull', 'origin', 'main'])
    if rc != 0:
        lines.append(f"git pull MISSLYCKADES: {out}")
        logging.error(f"harvest_self_update: git pull failed: {out}")
        return '\n'.join(lines)

    lines.append("git pull: OK")

    new_req_hash = ""
    if os.path.exists(req_path):
        with open(req_path, 'rb') as f:
            new_req_hash = hashlib.sha256(f.read()).hexdigest()

    if new_req_hash != old_req_hash:
        venv_pip = os.path.join(_PROJECT_DIR, 'venv', 'bin', 'pip')
        if os.path.exists(venv_pip):
            rc, out = _run([venv_pip, 'install', '-r', req_path, '--quiet'])
            if rc != 0:
                lines.append(f"pip install MISSLYCKADES: {out}")
                logging.error(f"harvest_self_update: pip install failed: {out}")
                return '\n'.join(lines)
            lines.append("Dependencies: uppdaterade")

            hash_file = os.path.join(_PROJECT_DIR, 'venv', '.requirements_hash')
            with open(hash_file, 'w') as f:
                import subprocess
                shasum = subprocess.run(
                    ['shasum', req_path], capture_output=True, text=True
                ).stdout.split()[0]
                f.write(shasum)
        else:
            lines.append("Dependencies: requirements.txt andrades men venv/bin/pip saknas — kor ./install.sh")
    else:
        lines.append("Dependencies: oforandrade")

    rc, diff_stat = _run(['git', 'diff', '--stat', f'{local_sha}..HEAD'])
    if diff_stat:
        lines.append(f"Andrade filer:\n{diff_stat}")

    lines.append("\nStarta om Claude Desktop for att ladda den uppdaterade servern.")

    logging.info(f"harvest_self_update: updated {local_sha[:8]} -> {remote_sha[:8]}, {commit_count} commits")
    return '\n'.join(lines)


# ======================================================================
# Boilerplate
# ======================================================================

def _is_broken_pipe(exc):
    """Rekursivt kolla om BrokenPipeError finns i exception chain."""
    if isinstance(exc, BrokenPipeError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_broken_pipe(e) for e in exc.exceptions)
    return False


if __name__ == "__main__":
    try:
        mcp.run()
    except BaseException as e:
        if _is_broken_pipe(e):
            os._exit(0)
        logging.critical(f"HarvestReports Server Crash: {e}")
        sys.exit(1)
