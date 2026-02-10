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
from harvest_client import HarvestClient

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

# Lazy-init client
_client = None


def _get_client() -> HarvestClient:
    """Lazy-initierad Harvest-klient."""
    global _client
    if _client is None:
        _client = HarvestClient(CONFIG['harvest'])
        logging.info("HarvestClient initialized")
    return _client


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
