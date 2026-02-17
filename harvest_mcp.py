"""
HarvestMCP - MCP-server för Harvest-rapporter.

Exponerar Harvest-data som MCP-verktyg för Claude Desktop, Cursor och andra AI-verktyg.
Fokus: Team utilization, tidsrapporter, projektöversikter.

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
mcp = FastMCP("HarvestReports")

# Lazy-init clients
_client = None
_forecast_client = None

# --- Prepare/commit draft storage ---
_drafts = {}  # draft_id → draft-objekt
_DRAFT_TTL_MINUTES = 30


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


# ======================================================================
# TOOL 1: Team Utilization
# ======================================================================

@mcp.tool()
def harvest_team_utilization(from_date: str = "", to_date: str = "") -> str:
    """
    Visa teamets belaggning och utilization for en period.

    Returnerar per person: totala timmar, billable/non-billable,
    kapacitet och utilization-procent.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
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

    lines = [
        f"## Team Utilization: {from_date} \u2192 {to_date}",
        f"*Period: {weeks:.1f} veckor*\n",
        "| Person | Billable | Non-bill | Totalt | Kapacitet | Util % |",
        "|--------|----------|----------|--------|-----------|--------|",
    ]

    for r in rows:
        lines.append(
            f"| {r['name']} | {r['billable']:.1f}h | {r['nonbill']:.1f}h | "
            f"{r['total']:.1f}h | {r['capacity']:.1f}h | {r['util']:.0f}% |"
        )

    avg_util = (total_billable / total_capacity * 100) if total_capacity > 0 else 0
    lines.append(
        f"| **Snitt/Totalt** | **{total_billable:.1f}h** | **{total_nonbill:.1f}h** | "
        f"**{total_hours:.1f}h** | **{total_capacity:.1f}h** | **{avg_util:.0f}%** |"
    )

    logging.info(f"harvest_team_utilization: {from_date} -> {to_date}, {len(rows)} personer")
    return '\n'.join(lines)


# ======================================================================
# TOOL 2: Vem jobbar med vad
# ======================================================================

@mcp.tool()
def harvest_who_works_on_what(
    from_date: str = "",
    to_date: str = "",
    group_by: str = "project"
) -> str:
    """
    Visa vem som jobbar med vad under en period.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        group_by: "project" (vilka jobbar per projekt) eller "person" (vilka projekt per person)
    """
    from_date, to_date = _resolve_dates(from_date, to_date)
    client = _get_client()

    entries = client.get_time_entries(from_date, to_date)

    if group_by == "person":
        return _format_by_person(entries, from_date, to_date)
    else:
        return _format_by_project(entries, from_date, to_date)


def _format_by_project(entries: list, from_date: str, to_date: str) -> str:
    """Gruppera tidsposter per projekt -> per person."""
    projects = defaultdict(lambda: {
        'client_name': '',
        'persons': defaultdict(lambda: {'hours': 0.0, 'billable_hours': 0.0}),
        'total_hours': 0.0,
    })

    for e in entries:
        proj = e.get('project', {}) or {}
        proj_name = proj.get('name', 'Unknown')
        client_obj = e.get('client', {}) or {}
        client_name = client_obj.get('name', '')
        user = e.get('user', {}) or {}
        person = user.get('name', 'Unknown')
        hours = e.get('hours', 0) or 0
        billable = e.get('billable', False)

        projects[proj_name]['client_name'] = client_name
        projects[proj_name]['persons'][person]['hours'] += hours
        if billable:
            projects[proj_name]['persons'][person]['billable_hours'] += hours
        projects[proj_name]['total_hours'] += hours

    sorted_projects = sorted(projects.items(), key=lambda x: x[1]['total_hours'], reverse=True)

    lines = [f"## Vem jobbar med vad: {from_date} \u2192 {to_date}\n"]

    for proj_name, data in sorted_projects:
        client_info = f" ({data['client_name']})" if data['client_name'] else ""
        lines.append(f"### {proj_name}{client_info} \u2014 {data['total_hours']:.1f}h totalt")
        lines.append("| Person | Timmar | Billable |")
        lines.append("|--------|--------|----------|")

        sorted_persons = sorted(
            data['persons'].items(),
            key=lambda x: x[1]['hours'],
            reverse=True
        )
        for person, pdata in sorted_persons:
            bill_str = f"{pdata['billable_hours']:.1f}h" if pdata['billable_hours'] > 0 else "\u2014"
            lines.append(f"| {person} | {pdata['hours']:.1f}h | {bill_str} |")

        lines.append("")

    logging.info(f"harvest_who_works_on_what(project): {len(sorted_projects)} projekt")
    return '\n'.join(lines)


def _format_by_person(entries: list, from_date: str, to_date: str) -> str:
    """Gruppera tidsposter per person -> per projekt."""
    persons = defaultdict(lambda: {
        'projects': defaultdict(lambda: {'hours': 0.0, 'client_name': ''}),
        'total_hours': 0.0,
    })

    for e in entries:
        proj = e.get('project', {}) or {}
        proj_name = proj.get('name', 'Unknown')
        client_obj = e.get('client', {}) or {}
        client_name = client_obj.get('name', '')
        user = e.get('user', {}) or {}
        person = user.get('name', 'Unknown')
        hours = e.get('hours', 0) or 0

        persons[person]['projects'][proj_name]['hours'] += hours
        persons[person]['projects'][proj_name]['client_name'] = client_name
        persons[person]['total_hours'] += hours

    sorted_persons = sorted(persons.items(), key=lambda x: x[1]['total_hours'], reverse=True)

    lines = [f"## Vem jobbar med vad (per person): {from_date} \u2192 {to_date}\n"]

    for person, data in sorted_persons:
        lines.append(f"### {person} \u2014 {data['total_hours']:.1f}h totalt")
        lines.append("| Projekt | Kund | Timmar |")
        lines.append("|---------|------|--------|")

        sorted_projects = sorted(
            data['projects'].items(),
            key=lambda x: x[1]['hours'],
            reverse=True
        )
        for proj_name, pdata in sorted_projects:
            lines.append(f"| {proj_name} | {pdata['client_name']} | {pdata['hours']:.1f}h |")

        lines.append("")

    logging.info(f"harvest_who_works_on_what(person): {len(sorted_persons)} personer")
    return '\n'.join(lines)


# ======================================================================
# TOOL 3: Flexibel tidssammanställning
# ======================================================================

@mcp.tool()
def harvest_time_summary(
    from_date: str = "",
    to_date: str = "",
    project_id: str = "",
    client_id: str = "",
    user_id: str = ""
) -> str:
    """
    Flexibel tidssammanstallning med valfria filter.

    Visar aggregerade timmar, billable/non-billable och belopp,
    grupperat per projekt och person.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        project_id: Filtrera pa projekt-ID (valfritt)
        client_id: Filtrera pa kund-ID (valfritt)
        user_id: Filtrera pa person-ID (valfritt)
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
        return f"Inga tidsposter hittades for {from_date} \u2192 {to_date} med angivna filter."

    total_hours = 0.0
    billable_hours = 0.0
    billable_amount = defaultdict(float)
    by_project = defaultdict(lambda: {'hours': 0.0, 'billable_hours': 0.0, 'persons': set()})

    for e in entries:
        hours = e.get('hours', 0) or 0
        total_hours += hours

        if e.get('billable', False):
            billable_hours += hours
            rate = e.get('billable_rate') or 0
            billable_amount['SEK'] += hours * rate

        proj = e.get('project', {}) or {}
        proj_name = proj.get('name', 'Unknown')
        user = e.get('user', {}) or {}
        person = user.get('name', 'Unknown')

        by_project[proj_name]['hours'] += hours
        if e.get('billable', False):
            by_project[proj_name]['billable_hours'] += hours
        by_project[proj_name]['persons'].add(person)

    nonbill_hours = total_hours - billable_hours

    lines = [
        f"## Tidssammanstallning: {from_date} \u2192 {to_date}\n",
        f"**Totalt:** {total_hours:.1f}h | **Billable:** {billable_hours:.1f}h | "
        f"**Non-billable:** {nonbill_hours:.1f}h",
    ]

    for currency, amount in billable_amount.items():
        if amount > 0:
            lines.append(f"**Billable belopp:** {amount:,.0f} {currency}")

    lines.append("")
    lines.append("| Projekt | Timmar | Billable | Personer |")
    lines.append("|---------|--------|----------|----------|")

    sorted_projects = sorted(by_project.items(), key=lambda x: x[1]['hours'], reverse=True)
    for proj_name, data in sorted_projects:
        persons_str = ', '.join(sorted(data['persons']))
        lines.append(
            f"| {proj_name} | {data['hours']:.1f}h | {data['billable_hours']:.1f}h | {persons_str} |"
        )

    logging.info(
        f"harvest_time_summary: {from_date} -> {to_date}, "
        f"{total_hours:.1f}h, {len(entries)} entries"
    )
    return '\n'.join(lines)


# ======================================================================
# TOOL 4: Lista projekt (hjälpverktyg)
# ======================================================================

@mcp.tool()
def harvest_list_projects(active_only: bool = True) -> str:
    """
    Lista alla Harvest-projekt med kund, status och budget-typ.

    Anvandbart for att hitta projekt-ID att filtrera med i andra verktyg.

    Args:
        active_only: Visa bara aktiva projekt (default: True)
    """
    client = _get_client()
    projects = client.get_projects(is_active=active_only)

    if not projects:
        return "Inga projekt hittades."

    lines = [
        f"## Harvest-projekt {'(aktiva)' if active_only else '(alla)'}\n",
        "| ID | Projekt | Kund | Budget | Billable |",
        "|----|---------|------|--------|----------|",
    ]

    for p in sorted(projects, key=lambda x: x.get('name', '')):
        client_obj = p.get('client')
        client_name = client_obj['name'] if client_obj else '\u2014'
        budget = p.get('budget') or '\u2014'
        budget_by = p.get('budget_by', '')
        if budget != '\u2014' and budget_by:
            budget = f"{budget} ({budget_by})"
        billable = 'Ja' if p.get('is_billable') else 'Nej'

        lines.append(f"| {p['id']} | {p['name']} | {client_name} | {budget} | {billable} |")

    logging.info(f"harvest_list_projects: {len(projects)} projekt")
    return '\n'.join(lines)


# ======================================================================
# TOOL 5: Lista användare (hjälpverktyg)
# ======================================================================

@mcp.tool()
def harvest_list_users(active_only: bool = True) -> str:
    """
    Lista alla Harvest-anvandare med roll och veckokapacitet.

    Anvandbart for att hitta user-ID att filtrera med i andra verktyg.

    Args:
        active_only: Visa bara aktiva anvandare (default: True)
    """
    client = _get_client()
    users = client.get_users(is_active=active_only)

    if not users:
        return "Inga anvandare hittades."

    lines = [
        f"## Harvest-anvandare {'(aktiva)' if active_only else '(alla)'}\n",
        "| ID | Namn | Roller | Kapacitet | Contractor |",
        "|----|------|--------|-----------|------------|",
    ]

    for u in sorted(users, key=lambda x: x.get('first_name', '')):
        name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
        roles = ', '.join(u.get('roles', [])) or '\u2014'
        cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
        contractor = 'Ja' if u.get('is_contractor') else 'Nej'

        lines.append(f"| {u['id']} | {name} | {roles} | {cap_h:.0f}h/v | {contractor} |")

    logging.info(f"harvest_list_users: {len(users)} anvandare")
    return '\n'.join(lines)


# ======================================================================
# TOOL 6: Forecast - Vem är schemalagd var
# ======================================================================

@mcp.tool()
def forecast_schedule(
    start_date: str = "",
    end_date: str = "",
    group_by: str = "person"
) -> str:
    """
    Visa vem som ar schemalagd pa vilka projekt i Forecast.

    Visar planerad allokering (timmar/dag) for varje person och projekt.
    Forecast ar ett resursplaneringsverktyg kopplat till Harvest.

    Args:
        start_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        end_date: Slutdatum YYYY-MM-DD (default: fredagen denna vecka)
        group_by: "person" (vilka projekt per person) eller "project" (vilka personer per projekt)
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

    # Lookup-tabeller
    people_map = {p['id']: p for p in people}
    project_map = {p['id']: p for p in projects}

    if group_by == "project":
        return _format_forecast_by_project(
            assignments, people_map, project_map, start_date, end_date
        )
    else:
        return _format_forecast_by_person(
            assignments, people_map, project_map, start_date, end_date
        )


def _format_forecast_by_person(
    assignments: list, people_map: dict, project_map: dict,
    start_date: str, end_date: str
) -> str:
    """Gruppera Forecast-assignments per person."""
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

        # allocation i sekunder per dag -> timmar totalt
        alloc_seconds = a.get('allocation', 0) or 0
        alloc_hours_per_day = alloc_seconds / 3600.0

        # Räkna arbetsdagar i assignmentets period (begränsat till sökt intervall)
        a_start = max(a.get('start_date', start_date), start_date)
        a_end = min(a.get('end_date', end_date), end_date)
        work_days = _count_work_days(a_start, a_end)
        total_hours = alloc_hours_per_day * work_days

        persons[person_name]['projects'][project_name] += total_hours
        persons[person_name]['total'] += total_hours

    sorted_persons = sorted(persons.items(), key=lambda x: x[1]['total'], reverse=True)

    lines = [f"## Forecast: {start_date} → {end_date}\n"]

    for person_name, data in sorted_persons:
        lines.append(f"### {person_name} — {data['total']:.1f}h planerat")
        lines.append("| Projekt | Timmar |")
        lines.append("|---------|--------|")
        for proj, hours in sorted(data['projects'].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {proj} | {hours:.1f}h |")
        lines.append("")

    if not sorted_persons:
        lines.append("Inga assignments hittades for perioden.")

    logging.info(f"forecast_schedule(person): {start_date} -> {end_date}, {len(sorted_persons)} personer")
    return '\n'.join(lines)


def _format_forecast_by_project(
    assignments: list, people_map: dict, project_map: dict,
    start_date: str, end_date: str
) -> str:
    """Gruppera Forecast-assignments per projekt."""
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

    lines = [f"## Forecast per projekt: {start_date} → {end_date}\n"]

    for proj_name, data in sorted_projects:
        lines.append(f"### {proj_name} — {data['total']:.1f}h planerat")
        lines.append("| Person | Timmar |")
        lines.append("|--------|--------|")
        for person, hours in sorted(data['persons'].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {person} | {hours:.1f}h |")
        lines.append("")

    if not sorted_projects:
        lines.append("Inga assignments hittades for perioden.")

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
# TOOL 7: Detaljerade tidsposter (med kommentarer)
# ======================================================================

@mcp.tool()
def harvest_detailed_time_entries(
    from_date: str = "",
    to_date: str = "",
    project_id: str = "",
    client_id: str = "",
    user_id: str = ""
) -> str:
    """
    Visa detaljerade tidsposter med kommentarer/notes.

    Till skillnad fran harvest_time_summary (som visar aggregerade timmar)
    visar detta verktyg varje enskild tidspost med datum, person, projekt,
    task och kommentar. Perfekt for att granska vad folk faktiskt gjort
    eller kontrollera att kommentarer finns.

    Args:
        from_date: Startdatum YYYY-MM-DD (default: mandagen denna vecka)
        to_date: Slutdatum YYYY-MM-DD (default: idag)
        project_id: Filtrera pa projekt-ID (valfritt)
        client_id: Filtrera pa kund-ID (valfritt)
        user_id: Filtrera pa person-ID (valfritt)
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
        return f"Inga tidsposter hittades for {from_date} \u2192 {to_date} med angivna filter."

    # Sortera: nyast datum forst, sedan person
    entries.sort(key=lambda e: (
        e.get('spent_date', ''),
        (e.get('user', {}) or {}).get('name', '')
    ), reverse=True)

    lines = [
        f"## Detaljerade tidsposter: {from_date} \u2192 {to_date}",
        f"*{len(entries)} poster*\n",
        "| Datum | Person | Projekt | Task | Timmar | Kommentar |",
        "|-------|--------|---------|------|--------|-----------|",
    ]

    total_hours = 0.0
    missing_notes = 0

    for e in entries:
        date = e.get('spent_date', '')
        user = e.get('user', {}) or {}
        person = user.get('name', 'Unknown')
        proj = e.get('project', {}) or {}
        proj_name = proj.get('name', 'Unknown')
        task = e.get('task', {}) or {}
        task_name = task.get('name', '')
        hours = e.get('hours', 0) or 0
        notes = (e.get('notes') or '').strip()

        total_hours += hours
        if not notes:
            missing_notes += 1

        # Trunkera langa kommentarer for tabellformat
        if len(notes) > 80:
            notes = notes[:77] + '...'
        # Escapea pipe-tecken i notes
        notes = notes.replace('|', '\\|')

        lines.append(
            f"| {date} | {person} | {proj_name} | {task_name} | {hours:.1f}h | {notes} |"
        )

    lines.append("")
    lines.append(f"**Totalt:** {total_hours:.1f}h | **Poster utan kommentar:** {missing_notes} av {len(entries)}")

    logging.info(
        f"harvest_detailed_time_entries: {from_date} -> {to_date}, "
        f"{len(entries)} entries, {missing_notes} utan notes"
    )
    return '\n'.join(lines)


# ======================================================================
# TOOL 8: Lista tasks för ett projekt
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
        f"## Tasks for projekt {project_id}\n",
        "| Task ID | Namn | Billable | Aktiv |",
        "|---------|------|----------|-------|",
    ]

    for a in assignments:
        task = a.get('task', {}) or {}
        task_id = task.get('id', '—')
        task_name = task.get('name', 'Unknown')
        billable = 'Ja' if a.get('billable') else 'Nej'
        active = 'Ja' if a.get('is_active') else 'Nej'
        lines.append(f"| {task_id} | {task_name} | {billable} | {active} |")

    logging.info(f"harvest_get_project_tasks: projekt {project_id}, {len(assignments)} tasks")
    return '\n'.join(lines)


# ======================================================================
# TOOL 9: Prepare timesheet (draft)
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

    # Cacha projektnamn + tasknamn for preview
    project_cache = {}
    task_cache = {}

    validated = []
    for i, entry in enumerate(entry_list):
        # Validera obligatoriska falt
        for field in ('project_id', 'task_id', 'spent_date', 'hours'):
            if field not in entry:
                raise ValueError(f"Entry {i}: saknar obligatoriskt falt '{field}'")

        # Validera datum
        try:
            datetime.strptime(entry['spent_date'], '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"Entry {i}: ogiltigt datumformat '{entry['spent_date']}'. Anvand YYYY-MM-DD.")

        # Validera hours
        if not isinstance(entry['hours'], (int, float)) or entry['hours'] <= 0:
            raise ValueError(f"Entry {i}: hours maste vara > 0, fick {entry['hours']}")

        # Notes obligatoriskt
        notes = (entry.get('notes') or '').strip()
        if not notes:
            raise ValueError(f"Entry {i}: notes ar obligatoriskt (projekt {entry['project_id']}, {entry['spent_date']})")

        # Sla upp projektnamn
        pid = entry['project_id']
        if pid not in project_cache:
            try:
                projects = client.get_projects(is_active=True)
                for p in projects:
                    project_cache[p['id']] = p['name']
            except Exception:
                project_cache[pid] = str(pid)

        # Sla upp tasknamn
        tid = entry['task_id']
        cache_key = (pid, tid)
        if cache_key not in task_cache:
            try:
                assignments = client.get_task_assignments(pid)
                for a in assignments:
                    t = a.get('task', {}) or {}
                    task_cache[(pid, t.get('id'))] = t.get('name', str(t.get('id')))
            except Exception:
                task_cache[cache_key] = str(tid)

        validated.append({
            'project_id': pid,
            'task_id': tid,
            'spent_date': entry['spent_date'],
            'hours': entry['hours'],
            'notes': notes,
            'project_name': project_cache.get(pid, str(pid)),
            'task_name': task_cache.get(cache_key, str(tid)),
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
        f"## Draft: {draft_id}",
        f"*{len(validated)} poster, {total_hours:.1f}h totalt*\n",
        "| # | Datum | Projekt | Task | Timmar | Notes |",
        "|---|-------|---------|------|--------|-------|",
    ]
    for i, e in enumerate(validated, 1):
        notes_preview = e['notes'][:60] + '...' if len(e['notes']) > 60 else e['notes']
        notes_preview = notes_preview.replace('|', '\\|')
        lines.append(
            f"| {i} | {e['spent_date']} | {e['project_name']} | {e['task_name']} | "
            f"{e['hours']:.1f}h | {notes_preview} |"
        )

    lines.append(f"\n**draft_id: `{draft_id}`** — giltig i {_DRAFT_TTL_MINUTES} minuter.")
    lines.append("Anropa `harvest_commit_timesheet(draft_id)` for att posta till Harvest.")

    logging.info(f"harvest_prepare_timesheet: draft {draft_id}, {len(validated)} entries, {total_hours:.1f}h")
    return '\n'.join(lines)


# ======================================================================
# TOOL 10: Commit timesheet (draft -> Harvest)
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
            # STOPP — rapportera var det gick fel
            draft['committed'] = True  # Markera for att forhindra retry
            lines = [
                f"## Commit AVBRUTEN vid rad {i+1} av {len(draft['entries'])}",
                f"**Fel:** {e}\n",
            ]
            if results:
                lines.append("Lyckade poster (redan i Harvest):")
                lines.append("| # | Datum | Projekt | Timmar | Entry ID |")
                lines.append("|---|-------|---------|--------|----------|")
                lines.extend(results)
            lines.append(f"\n**{len(draft['entries']) - i} poster EJ postade.** Korrigera och skapa ny draft.")
            logging.error(f"harvest_commit_timesheet: draft {draft_id} failed at entry {i}: {e}")
            return '\n'.join(lines)

    draft['committed'] = True

    lines = [
        f"## Commit klar: {draft_id}",
        f"*{len(results)} poster postade till Harvest*\n",
        "| # | Datum | Projekt | Timmar | Entry ID |",
        "|---|-------|---------|--------|----------|",
    ]
    lines.extend(results)

    logging.info(f"harvest_commit_timesheet: draft {draft_id}, {len(results)} entries committed")
    return '\n'.join(lines)


# ======================================================================
# TOOL 11: Uppdatera tidspost
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
        f"Tidspost uppdaterad: entry_id={entry_id} | {proj_name} | "
        f"{updated_hours}h | uppdaterade falt: {', '.join(fields.keys())}"
    )


# ======================================================================
# TOOL 12: Ta bort tidspost
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
    return f"Tidspost borttagen: entry_id={entry_id}"


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
