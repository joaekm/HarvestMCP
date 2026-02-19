"""
Tester for harvest_mcp.py — alla MCP-verktyg.

Kör: venv/bin/pytest test_harvest_mcp.py -v

Alla API-anrop mockas. Inga riktiga Harvest/Forecast-anrop görs.
"""

import json
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Mocka bort modulnivå-sidoeffekter INNAN import
# ---------------------------------------------------------------------------

# harvest_auth.load_config behöver mockas innan harvest_mcp importeras
_FAKE_CONFIG = {
    'harvest': {
        'api_base_url': 'https://api.harvestapp.com/v2',
        'client_id': 'fake',
        'client_secret': 'fake',
        'token_path': '/tmp/fake_token.json',
        'user_agent': 'TestAgent',
    },
    'forecast': {
        'api_base_url': 'https://api.forecastapp.com',
        'client_id': 'fake',
        'client_secret': 'fake',
        'token_path': '/tmp/fake_fc_token.json',
        'user_agent': 'TestAgent',
    },
}

# Patch load_config och klienternas __init__ INNAN import
with patch('harvest_auth.load_config', return_value=_FAKE_CONFIG):
    with patch('harvest_client.HarvestClient.__init__', return_value=None):
        with patch('harvest_client.ForecastClient.__init__', return_value=None):
            import harvest_mcp
            from harvest_mcp import (
                _resolve_dates,
                _weeks_in_range,
                _truncation_note,
                _count_work_days,
                _fuzzy_match,
                _format_summary,
                _format_by_project,
                _format_by_person,
                _format_forecast_by_person,
                _format_forecast_by_project,
                _cleanup_expired_drafts,
                _drafts,
                _DRAFT_TTL_MINUTES,
            )


# ---------------------------------------------------------------------------
# Testdata-fabriker
# ---------------------------------------------------------------------------

def _make_time_entry(
    spent_date="2026-02-16",
    hours=4.0,
    billable=True,
    billable_rate=1000,
    project_name="Projekt A",
    project_id=100,
    client_name="Kund X",
    user_name="Anna Andersson",
    task_name="Utveckling",
    notes="Jobbade med feature",
    entry_id=9001,
):
    return {
        'id': entry_id,
        'spent_date': spent_date,
        'hours': hours,
        'billable': billable,
        'billable_rate': billable_rate if billable else None,
        'project': {'id': project_id, 'name': project_name},
        'client': {'name': client_name},
        'user': {'name': user_name},
        'task': {'name': task_name},
        'notes': notes,
    }


def _make_team_report_entry(user_id=1, user_name="Anna Andersson", billable_hours=30, total_hours=40):
    return {
        'user_id': user_id,
        'user_name': user_name,
        'billable_hours': billable_hours,
        'total_hours': total_hours,
    }


def _make_user(user_id=1, first_name="Anna", last_name="Andersson", weekly_capacity_h=40, is_active=True):
    return {
        'id': user_id,
        'first_name': first_name,
        'last_name': last_name,
        'weekly_capacity': weekly_capacity_h * 3600,
        'roles': ['Member'],
        'is_contractor': False,
        'is_active': is_active,
    }


def _make_role(role_id=1, name="Team Alpha", user_ids=None):
    return {
        'id': role_id,
        'name': name,
        'user_ids': user_ids or [],
        'created_at': '2026-01-01T00:00:00Z',
        'updated_at': '2026-01-01T00:00:00Z',
    }


def _make_project(project_id=100, name="Projekt A", client_name="Kund X", is_billable=True):
    return {
        'id': project_id,
        'name': name,
        'client': {'name': client_name},
        'is_billable': is_billable,
        'budget': None,
        'budget_by': '',
    }


def _make_forecast_assignment(person_id=1, project_id=10, allocation_h=8, start_date="2026-02-16", end_date="2026-02-20"):
    return {
        'person_id': person_id,
        'project_id': project_id,
        'allocation': allocation_h * 3600,
        'start_date': start_date,
        'end_date': end_date,
    }


def _make_forecast_person(person_id=1, first_name="Anna", last_name="Andersson"):
    return {'id': person_id, 'first_name': first_name, 'last_name': last_name}


def _make_forecast_project(project_id=10, name="FC Projekt"):
    return {'id': project_id, 'name': name}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestResolvedDates:
    """Tester for _resolve_dates."""

    def test_explicit_dates(self):
        f, t = _resolve_dates("2026-01-01", "2026-01-31")
        assert f == "2026-01-01"
        assert t == "2026-01-31"

    def test_default_dates(self):
        f, t = _resolve_dates("", "")
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        assert f == monday.isoformat()
        assert t == today.isoformat()

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Ogiltigt datumformat"):
            _resolve_dates("not-a-date", "2026-01-01")

    def test_invalid_to_date(self):
        with pytest.raises(ValueError, match="Ogiltigt datumformat"):
            _resolve_dates("2026-01-01", "31-01-2026")


class TestWeeksInRange:
    def test_one_week(self):
        assert _weeks_in_range("2026-02-16", "2026-02-22") == 1.0

    def test_one_day(self):
        result = _weeks_in_range("2026-02-16", "2026-02-16")
        assert abs(result - 1 / 7) < 0.01


class TestCountWorkDays:
    def test_full_week(self):
        assert _count_work_days("2026-02-16", "2026-02-20") == 5

    def test_weekend(self):
        # lör-sön
        assert _count_work_days("2026-02-21", "2026-02-22") == 0

    def test_single_weekday(self):
        # måndag
        assert _count_work_days("2026-02-16", "2026-02-16") == 1


class TestTruncationNote:
    def test_note(self):
        note = _truncation_note(10, 50)
        assert "10 av 50" in note
        assert "40 dolda" in note


# ---------------------------------------------------------------------------
# Format-funktioner (ren logik, ingen mock behövs)
# ---------------------------------------------------------------------------

class TestFormatSummary:
    def test_basic(self):
        entries = [_make_time_entry(), _make_time_entry(hours=2, billable=False)]
        result = _format_summary(entries, "2026-02-16", "2026-02-16", 30)
        assert "Totalt: 6.0h" in result
        assert "Billable: 4.0h" in result
        assert "Projekt A" in result

    def test_max_rows_truncation(self):
        entries = [
            _make_time_entry(project_name=f"Proj{i}", project_id=i)
            for i in range(10)
        ]
        result = _format_summary(entries, "2026-02-16", "2026-02-16", 3)
        assert "Visar 3 av 10" in result

    def test_max_rows_zero_shows_all(self):
        entries = [
            _make_time_entry(project_name=f"Proj{i}", project_id=i)
            for i in range(10)
        ]
        result = _format_summary(entries, "2026-02-16", "2026-02-16", 0)
        assert "Visar" not in result
        for i in range(10):
            assert f"Proj{i}" in result

    def test_billable_amount(self):
        entries = [_make_time_entry(hours=10, billable=True, billable_rate=1500)]
        result = _format_summary(entries, "2026-02-16", "2026-02-16", 30)
        assert "15,000 SEK" in result

    def test_persons_count_not_names(self):
        """Summary mode should show person COUNT, not list of names."""
        entries = [
            _make_time_entry(user_name="Anna", hours=5),
            _make_time_entry(user_name="Bo", hours=3),
            _make_time_entry(user_name="Clara", hours=2),
        ]
        result = _format_summary(entries, "2026-02-16", "2026-02-16", 30)
        assert "| Pers |" in result
        assert "| 3 |" in result
        # Should NOT list individual names in summary mode
        assert "Anna" not in result


class TestFormatByProject:
    def test_grouped(self):
        entries = [
            _make_time_entry(user_name="Anna", hours=5),
            _make_time_entry(user_name="Bo", hours=3),
        ]
        result = _format_by_project(entries, "2026-02-16", "2026-02-16", 30)
        assert "Projekt A" in result
        assert "Anna" in result
        assert "Bo" in result

    def test_truncation(self):
        entries = [
            _make_time_entry(project_name=f"P{i}", project_id=i)
            for i in range(5)
        ]
        result = _format_by_project(entries, "2026-02-16", "2026-02-16", 2)
        assert "Visar 2 av 5" in result


class TestFormatByPerson:
    def test_grouped(self):
        entries = [
            _make_time_entry(user_name="Anna", project_name="P1", hours=5),
            _make_time_entry(user_name="Anna", project_name="P2", hours=3),
            _make_time_entry(user_name="Bo", project_name="P1", hours=2),
        ]
        result = _format_by_person(entries, "2026-02-16", "2026-02-16", 30)
        assert "Anna" in result
        assert "Bo" in result
        assert "P1" in result
        assert "P2" in result

    def test_sort_order(self):
        entries = [
            _make_time_entry(user_name="Bo", hours=1),
            _make_time_entry(user_name="Anna", hours=10),
        ]
        result = _format_by_person(entries, "2026-02-16", "2026-02-16", 30)
        anna_pos = result.index("Anna")
        bo_pos = result.index("Bo")
        assert anna_pos < bo_pos  # Anna har fler timmar, ska komma först


# ---------------------------------------------------------------------------
# Forecast-formatering
# ---------------------------------------------------------------------------

class TestFormatForecastByPerson:
    def test_basic(self):
        assignments = [_make_forecast_assignment()]
        people = {1: _make_forecast_person()}
        projects = {10: _make_forecast_project()}
        result = _format_forecast_by_person(assignments, people, projects, "2026-02-16", "2026-02-20")
        assert "Anna Andersson" in result
        assert "FC Projekt" in result
        assert "40.0h" in result  # 8h/dag * 5 dagar

    def test_empty(self):
        result = _format_forecast_by_person([], {}, {}, "2026-02-16", "2026-02-20")
        assert "Inga assignments" in result


class TestFormatForecastByProject:
    def test_basic(self):
        assignments = [_make_forecast_assignment()]
        people = {1: _make_forecast_person()}
        projects = {10: _make_forecast_project()}
        result = _format_forecast_by_project(assignments, people, projects, "2026-02-16", "2026-02-20")
        assert "FC Projekt" in result
        assert "Anna Andersson" in result

    def test_empty(self):
        result = _format_forecast_by_project([], {}, {}, "2026-02-16", "2026-02-20")
        assert "Inga assignments" in result


# ---------------------------------------------------------------------------
# Draft-hantering
# ---------------------------------------------------------------------------

class TestDraftLifecycle:
    def setup_method(self):
        _drafts.clear()

    def test_cleanup_expired(self):
        _drafts['old'] = {
            'created_at': datetime.now() - timedelta(minutes=_DRAFT_TTL_MINUTES + 1),
            'entries': [],
            'user_id': None,
            'committed': False,
        }
        _drafts['fresh'] = {
            'created_at': datetime.now(),
            'entries': [],
            'user_id': None,
            'committed': False,
        }
        _cleanup_expired_drafts()
        assert 'old' not in _drafts
        assert 'fresh' in _drafts

    def teardown_method(self):
        _drafts.clear()


# ---------------------------------------------------------------------------
# MCP-verktyg med mockade API-klienter
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_harvest_client():
    """Mocka _get_client() att returnera en MagicMock."""
    client = MagicMock()
    with patch.object(harvest_mcp, '_get_client', return_value=client):
        yield client


@pytest.fixture
def mock_forecast_client():
    """Mocka _get_forecast_client() att returnera en MagicMock."""
    client = MagicMock()
    with patch.object(harvest_mcp, '_get_forecast_client', return_value=client):
        yield client


class TestHarvestTeamUtilization:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_report_time_by_team.return_value = [
            _make_team_report_entry(user_id=1, user_name="Anna", billable_hours=30, total_hours=40),
            _make_team_report_entry(user_id=2, user_name="Bo", billable_hours=10, total_hours=35),
        ]
        mock_harvest_client.get_users.return_value = [
            _make_user(user_id=1),
            _make_user(user_id=2, first_name="Bo", last_name="Berg"),
        ]

        result = harvest_mcp.harvest_team_utilization("2026-02-16", "2026-02-22")
        assert "Anna" in result
        assert "Bo" in result
        assert "Util%" in result
        assert "**Totalt**" in result

    def test_empty_team(self, mock_harvest_client):
        mock_harvest_client.get_report_time_by_team.return_value = []
        mock_harvest_client.get_users.return_value = []

        result = harvest_mcp.harvest_team_utilization("2026-02-16", "2026-02-22")
        assert "Team Utilization" in result

    def test_max_rows(self, mock_harvest_client):
        mock_harvest_client.get_report_time_by_team.return_value = [
            _make_team_report_entry(user_id=i, user_name=f"Person{i}", billable_hours=10, total_hours=20)
            for i in range(10)
        ]
        mock_harvest_client.get_users.return_value = [
            _make_user(user_id=i, first_name=f"Person{i}") for i in range(10)
        ]

        result = harvest_mcp.harvest_team_utilization("2026-02-16", "2026-02-22", max_rows=3)
        assert "Visar 3 av 10" in result
        # Totals row should still be present
        assert "**Totalt**" in result

    def test_max_rows_zero_shows_all(self, mock_harvest_client):
        mock_harvest_client.get_report_time_by_team.return_value = [
            _make_team_report_entry(user_id=i, user_name=f"Person{i}")
            for i in range(5)
        ]
        mock_harvest_client.get_users.return_value = [
            _make_user(user_id=i, first_name=f"Person{i}") for i in range(5)
        ]

        result = harvest_mcp.harvest_team_utilization("2026-02-16", "2026-02-22", max_rows=0)
        assert "Visar" not in result
        for i in range(5):
            assert f"Person{i}" in result


class TestHarvestTimeSummary:
    def test_summary_mode(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(hours=5),
            _make_time_entry(hours=3, project_name="Projekt B", project_id=200),
        ]
        result = harvest_mcp.harvest_time_summary("2026-02-16", "2026-02-16", group_by="summary")
        assert "Totalt: 8.0h" in result
        assert "Projekt A" in result
        assert "Projekt B" in result

    def test_project_mode(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(user_name="Anna", hours=5),
            _make_time_entry(user_name="Bo", hours=3),
        ]
        result = harvest_mcp.harvest_time_summary("2026-02-16", "2026-02-16", group_by="project")
        assert "Per projekt" in result
        assert "Anna" in result

    def test_person_mode(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(user_name="Anna", hours=5),
        ]
        result = harvest_mcp.harvest_time_summary("2026-02-16", "2026-02-16", group_by="person")
        assert "Per person" in result
        assert "Anna" in result

    def test_no_entries(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = []
        result = harvest_mcp.harvest_time_summary("2026-02-16", "2026-02-16")
        assert "Inga tidsposter" in result

    def test_filters_passed(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [_make_time_entry()]
        harvest_mcp.harvest_time_summary(
            "2026-02-16", "2026-02-16",
            project_id="100", user_id="1"
        )
        _, kwargs = mock_harvest_client.get_time_entries.call_args
        assert kwargs['project_id'] == '100'
        assert kwargs['user_id'] == '1'

    def test_max_rows(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(project_name=f"P{i}", project_id=i) for i in range(10)
        ]
        result = harvest_mcp.harvest_time_summary(
            "2026-02-16", "2026-02-16", group_by="summary", max_rows=3
        )
        assert "Visar 3 av 10" in result


class TestHarvestDetailedTimeEntries:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(entry_id=1001, notes="Arbetat med X"),
            _make_time_entry(entry_id=1002, notes=""),
        ]
        result = harvest_mcp.harvest_detailed_time_entries("2026-02-16", "2026-02-16")
        assert "1001" in result
        assert "1002" in result
        assert "2 poster" in result
        assert "1 utan kommentar" in result

    def test_max_rows(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(entry_id=i) for i in range(50)
        ]
        result = harvest_mcp.harvest_detailed_time_entries(
            "2026-02-16", "2026-02-16", max_rows=10
        )
        assert "Visar 10 av 50" in result
        assert "50 poster" in result  # Totalt visas alltid i header

    def test_no_entries(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = []
        result = harvest_mcp.harvest_detailed_time_entries("2026-02-16", "2026-02-16")
        assert "Inga tidsposter" in result

    def test_long_notes_truncated(self, mock_harvest_client):
        long_note = "A" * 100
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(notes=long_note)
        ]
        result = harvest_mcp.harvest_detailed_time_entries("2026-02-16", "2026-02-16")
        assert "..." in result
        assert long_note not in result

    def test_pipe_in_notes_escaped(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(notes="test|with|pipes")
        ]
        result = harvest_mcp.harvest_detailed_time_entries("2026-02-16", "2026-02-16")
        assert "test\\|with\\|pipes" in result

    def test_entry_id_in_output(self, mock_harvest_client):
        mock_harvest_client.get_time_entries.return_value = [
            _make_time_entry(entry_id=42)
        ]
        result = harvest_mcp.harvest_detailed_time_entries("2026-02-16", "2026-02-16")
        assert "42" in result
        assert "| ID |" in result


class TestHarvestListProjects:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Alpha", "Kund A"),
            _make_project(200, "Beta", "Kund B", is_billable=False),
        ]
        result = harvest_mcp.harvest_list_projects()
        assert "Alpha" in result
        assert "Beta" in result
        assert "2 st" in result

    def test_max_rows(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(i, f"P{i}") for i in range(10)
        ]
        result = harvest_mcp.harvest_list_projects(max_rows=3)
        assert "Visar 3 av 10" in result

    def test_empty(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = []
        result = harvest_mcp.harvest_list_projects()
        assert "Inga projekt" in result


class TestHarvestListUsers:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "A"),
            _make_user(2, "Bo", "B"),
        ]
        result = harvest_mcp.harvest_list_users()
        assert "Anna" in result
        assert "Bo" in result
        assert "2 st" in result

    def test_max_rows(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(i, f"User{i}") for i in range(10)
        ]
        result = harvest_mcp.harvest_list_users(max_rows=2)
        assert "Visar 2 av 10" in result

    def test_empty(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = []
        result = harvest_mcp.harvest_list_users()
        assert "Inga anvandare" in result


class TestFuzzyMatch:
    def test_exact(self):
        assert _fuzzy_match("Besqab", "Besqab") is True

    def test_case_insensitive(self):
        assert _fuzzy_match("besqab", "Besqab Projekt") is True

    def test_substring(self):
        assert _fuzzy_match("proj", "Stort Projekt AB") is True

    def test_no_match(self):
        assert _fuzzy_match("xyz", "Besqab") is False


class TestHarvestFindProject:
    def test_match_by_name(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Besqab Nyproduktion", "Besqab AB"),
            _make_project(200, "Internt Projekt", "Eget"),
        ]
        result = harvest_mcp.harvest_find_project("besqab")
        assert "100" in result
        assert "Besqab Nyproduktion" in result
        assert "Internt" not in result

    def test_match_by_client(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Webbplats", "Acme AB"),
            _make_project(200, "App", "Beta Corp"),
        ]
        result = harvest_mcp.harvest_find_project("acme")
        assert "100" in result
        assert "Webbplats" in result
        assert "App" not in result

    def test_multiple_matches(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "AI Connector", "Kund A"),
            _make_project(200, "AI Platform", "Kund B"),
            _make_project(300, "Webbshop", "Kund C"),
        ]
        result = harvest_mcp.harvest_find_project("ai")
        assert "100" in result
        assert "200" in result
        assert "300" not in result

    def test_no_match(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Projekt A"),
        ]
        result = harvest_mcp.harvest_find_project("xyz")
        assert "Inga projekt matchade" in result

    def test_compact_output(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Proj A", "Kund X"),
        ]
        result = harvest_mcp.harvest_find_project("proj")
        # Should NOT contain markdown table headers
        assert "| ID |" not in result
        assert "|---" not in result
        # Should be minimal: just id | name | client per line
        lines = result.strip().split('\n')
        assert len(lines) == 1

    def test_active_only_false(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Gammalt Projekt", "Kund A"),
        ]
        harvest_mcp.harvest_find_project("gammalt", active_only=False)
        mock_harvest_client.get_projects.assert_called_with(is_active=False)

    def test_active_only_default_true(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = []
        harvest_mcp.harvest_find_project("test")
        mock_harvest_client.get_projects.assert_called_with(is_active=True)

    def test_no_match_hint_when_active_only(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = []
        result = harvest_mcp.harvest_find_project("xyz", active_only=True)
        assert "active_only=false" in result

    def test_no_match_no_hint_when_all(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = []
        result = harvest_mcp.harvest_find_project("xyz", active_only=False)
        assert "active_only=false" not in result

    def test_inactive_marker_shown(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            {**_make_project(100, "Old Proj", "Kund A"), 'is_active': False},
            {**_make_project(200, "Active Proj", "Kund B"), 'is_active': True},
        ]
        result = harvest_mcp.harvest_find_project("proj", active_only=False)
        assert "[inaktiv]" in result
        assert "[aktiv]" in result

    def test_no_marker_when_active_only(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [
            _make_project(100, "Proj A", "Kund X"),
        ]
        result = harvest_mcp.harvest_find_project("proj", active_only=True)
        assert "[aktiv]" not in result
        assert "[inaktiv]" not in result


class TestHarvestFindUser:
    def test_match(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "Andersson"),
            _make_user(2, "Bo", "Berg"),
        ]
        result = harvest_mcp.harvest_find_user("anna")
        assert "1" in result
        assert "Anna" in result
        assert "Bo" not in result

    def test_match_lastname(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "Andersson"),
            _make_user(2, "Bo", "Berg"),
        ]
        result = harvest_mcp.harvest_find_user("berg")
        assert "2" in result
        assert "Bo" in result

    def test_no_match(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "Andersson"),
        ]
        result = harvest_mcp.harvest_find_user("xyz")
        assert "Inga anvandare matchade" in result

    def test_compact_output(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "Andersson"),
        ]
        result = harvest_mcp.harvest_find_user("anna")
        assert "| ID |" not in result
        lines = result.strip().split('\n')
        assert len(lines) == 1

    def test_active_only_false(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = [
            _make_user(1, "Anna", "Andersson"),
        ]
        harvest_mcp.harvest_find_user("anna", active_only=False)
        mock_harvest_client.get_users.assert_called_with(is_active=False)

    def test_active_only_default_true(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = []
        harvest_mcp.harvest_find_user("test")
        mock_harvest_client.get_users.assert_called_with(is_active=True)

    def test_no_match_hint_when_active_only(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = []
        result = harvest_mcp.harvest_find_user("xyz", active_only=True)
        assert "active_only=false" in result

    def test_no_match_no_hint_when_all(self, mock_harvest_client):
        mock_harvest_client.get_users.return_value = []
        result = harvest_mcp.harvest_find_user("xyz", active_only=False)
        assert "active_only=false" not in result


class TestForecastSchedule:
    def test_by_person(self, mock_forecast_client):
        mock_forecast_client.get_assignments.return_value = [
            _make_forecast_assignment(person_id=1, project_id=10, allocation_h=8),
        ]
        mock_forecast_client.get_people.return_value = [_make_forecast_person(1)]
        mock_forecast_client.get_projects.return_value = [_make_forecast_project(10)]

        result = harvest_mcp.forecast_schedule("2026-02-16", "2026-02-20", group_by="person")
        assert "Anna Andersson" in result
        assert "FC Projekt" in result

    def test_by_project(self, mock_forecast_client):
        mock_forecast_client.get_assignments.return_value = [
            _make_forecast_assignment(person_id=1, project_id=10),
        ]
        mock_forecast_client.get_people.return_value = [_make_forecast_person(1)]
        mock_forecast_client.get_projects.return_value = [_make_forecast_project(10)]

        result = harvest_mcp.forecast_schedule("2026-02-16", "2026-02-20", group_by="project")
        assert "Forecast per projekt" in result
        assert "FC Projekt" in result

    def test_empty(self, mock_forecast_client):
        mock_forecast_client.get_assignments.return_value = []
        mock_forecast_client.get_people.return_value = []
        mock_forecast_client.get_projects.return_value = []

        result = harvest_mcp.forecast_schedule("2026-02-16", "2026-02-20")
        assert "Inga assignments" in result

    def test_max_rows_by_person(self, mock_forecast_client):
        mock_forecast_client.get_assignments.return_value = [
            _make_forecast_assignment(person_id=i, project_id=10, allocation_h=8)
            for i in range(1, 11)
        ]
        mock_forecast_client.get_people.return_value = [
            _make_forecast_person(i, f"Person{i}") for i in range(1, 11)
        ]
        mock_forecast_client.get_projects.return_value = [_make_forecast_project(10)]

        result = harvest_mcp.forecast_schedule(
            "2026-02-16", "2026-02-20", group_by="person", max_rows=3
        )
        assert "Visar 3 av 10" in result

    def test_max_rows_by_project(self, mock_forecast_client):
        mock_forecast_client.get_assignments.return_value = [
            _make_forecast_assignment(person_id=1, project_id=i, allocation_h=8)
            for i in range(10, 20)
        ]
        mock_forecast_client.get_people.return_value = [_make_forecast_person(1)]
        mock_forecast_client.get_projects.return_value = [
            _make_forecast_project(i, f"Proj{i}") for i in range(10, 20)
        ]

        result = harvest_mcp.forecast_schedule(
            "2026-02-16", "2026-02-20", group_by="project", max_rows=3
        )
        assert "Visar 3 av 10" in result


class TestHarvestGetProjectTasks:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_task_assignments.return_value = [
            {'task': {'id': 501, 'name': 'Utveckling'}, 'billable': True, 'is_active': True},
            {'task': {'id': 502, 'name': 'Admin'}, 'billable': False, 'is_active': True},
        ]
        result = harvest_mcp.harvest_get_project_tasks(100)
        assert "501" in result
        assert "Utveckling" in result
        assert "Admin" in result

    def test_empty(self, mock_harvest_client):
        mock_harvest_client.get_task_assignments.return_value = []
        result = harvest_mcp.harvest_get_project_tasks(999)
        assert "Inga tasks" in result


class TestHarvestListTeams:
    def test_basic(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Drive", [101, 102, 103]),
            _make_role(2, "Team AI", [201, 202]),
        ]
        result = harvest_mcp.harvest_list_teams()
        assert "2 st" in result
        assert "Team Drive" in result
        assert "Team AI" in result
        assert "| 3 |" in result  # 3 members in Team Drive
        assert "| 2 |" in result  # 2 members in Team AI

    def test_empty(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = []
        result = harvest_mcp.harvest_list_teams()
        assert "Inga roller" in result

    def test_sorted_by_name(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(2, "Zebra Team", [1]),
            _make_role(1, "Alpha Team", [1]),
        ]
        result = harvest_mcp.harvest_list_teams()
        alpha_pos = result.index("Alpha Team")
        zebra_pos = result.index("Zebra Team")
        assert alpha_pos < zebra_pos


class TestHarvestGetTeam:
    def test_match(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Drive (Drive Unit)", [101, 102]),
            _make_role(2, "Team AI (AI Unit)", [201]),
        ]
        mock_harvest_client.get_users.side_effect = [
            # First call: active users
            [_make_user(101, "Anna", "A"), _make_user(102, "Bo", "B")],
            # Second call: inactive users
            [],
        ]
        result = harvest_mcp.harvest_get_team("drive")
        assert "Team Drive" in result
        assert "Anna" in result
        assert "Bo" in result
        assert "Team AI" not in result

    def test_no_match(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Drive", [101]),
        ]
        result = harvest_mcp.harvest_get_team("xyz")
        assert "Inget team matchade" in result

    def test_shows_inactive_users(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Drive", [101, 102]),
        ]
        mock_harvest_client.get_users.side_effect = [
            # Active users
            [_make_user(101, "Anna", "A", is_active=True)],
            # Inactive users
            [_make_user(102, "Bo", "B", is_active=False)],
        ]
        result = harvest_mcp.harvest_get_team("drive")
        assert "Anna" in result
        assert "Bo" in result
        assert "Nej" in result  # Bo is inactive

    def test_unknown_user_id(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Drive", [101, 999]),
        ]
        mock_harvest_client.get_users.side_effect = [
            [_make_user(101, "Anna", "A")],
            [],
        ]
        result = harvest_mcp.harvest_get_team("drive")
        assert "Anna" in result
        assert "(okand)" in result

    def test_multiple_matches(self, mock_harvest_client):
        mock_harvest_client.get_roles.return_value = [
            _make_role(1, "Team Alpha", [101]),
            _make_role(2, "Team Omega", [201]),
        ]
        mock_harvest_client.get_users.side_effect = [
            [_make_user(101, "Anna", "A"), _make_user(201, "Bo", "B")],
            [],
        ]
        result = harvest_mcp.harvest_get_team("team")
        assert "Team Alpha" in result
        assert "Team Omega" in result


class TestHarvestPrepareTimesheet:
    def setup_method(self):
        _drafts.clear()

    def test_valid_entries(self, mock_harvest_client):
        mock_harvest_client.get_projects.return_value = [_make_project(100, "Proj A")]
        mock_harvest_client.get_task_assignments.return_value = [
            {'task': {'id': 501, 'name': 'Dev'}, 'billable': True, 'is_active': True},
        ]

        entries = json.dumps([{
            'project_id': 100, 'task_id': 501,
            'spent_date': '2026-02-16', 'hours': 4.0,
            'notes': 'Worked on feature',
        }])
        result = harvest_mcp.harvest_prepare_timesheet(entries)
        assert "Draft:" in result
        assert "1 poster" in result
        assert "4.0h" in result
        assert len(_drafts) == 1

    def test_invalid_json(self, mock_harvest_client):
        with pytest.raises(ValueError, match="Ogiltigt JSON"):
            harvest_mcp.harvest_prepare_timesheet("not json")

    def test_empty_list(self, mock_harvest_client):
        with pytest.raises(ValueError, match="icke-tom"):
            harvest_mcp.harvest_prepare_timesheet("[]")

    def test_missing_field(self, mock_harvest_client):
        entries = json.dumps([{'project_id': 100, 'task_id': 501, 'spent_date': '2026-02-16'}])
        with pytest.raises(ValueError, match="saknar obligatoriskt"):
            harvest_mcp.harvest_prepare_timesheet(entries)

    def test_invalid_date(self, mock_harvest_client):
        entries = json.dumps([{
            'project_id': 100, 'task_id': 501,
            'spent_date': 'not-a-date', 'hours': 4.0,
        }])
        with pytest.raises(ValueError, match="ogiltigt datumformat"):
            harvest_mcp.harvest_prepare_timesheet(entries)

    def test_zero_hours(self, mock_harvest_client):
        entries = json.dumps([{
            'project_id': 100, 'task_id': 501,
            'spent_date': '2026-02-16', 'hours': 0,
        }])
        with pytest.raises(ValueError, match="hours maste vara > 0"):
            harvest_mcp.harvest_prepare_timesheet(entries)

    def teardown_method(self):
        _drafts.clear()


class TestHarvestCommitTimesheet:
    def setup_method(self):
        _drafts.clear()

    def test_commit_success(self, mock_harvest_client):
        # Skapa en draft manuellt
        _drafts['test-id'] = {
            'entries': [{
                'project_id': 100, 'task_id': 501,
                'spent_date': '2026-02-16', 'hours': 4.0,
                'notes': 'Test', 'project_name': 'Proj A', 'task_name': 'Dev',
            }],
            'user_id': None,
            'created_at': datetime.now(),
            'committed': False,
        }
        mock_harvest_client.create_time_entry.return_value = {'id': 8001}

        result = harvest_mcp.harvest_commit_timesheet('test-id')
        assert "Commit klar" in result
        assert "8001" in result
        assert _drafts['test-id']['committed'] is True

    def test_missing_draft(self):
        with pytest.raises(ValueError, match="finns inte"):
            harvest_mcp.harvest_commit_timesheet('nonexistent')

    def test_already_committed(self):
        _drafts['done'] = {
            'entries': [],
            'user_id': None,
            'created_at': datetime.now(),
            'committed': True,
        }
        with pytest.raises(ValueError, match="redan committats"):
            harvest_mcp.harvest_commit_timesheet('done')

    def test_expired_draft(self):
        _drafts['expired'] = {
            'entries': [],
            'user_id': None,
            'created_at': datetime.now() - timedelta(minutes=_DRAFT_TTL_MINUTES + 5),
            'committed': False,
        }
        with pytest.raises(ValueError, match="expired"):
            harvest_mcp.harvest_commit_timesheet('expired')

    def test_partial_failure(self, mock_harvest_client):
        _drafts['partial'] = {
            'entries': [
                {'project_id': 100, 'task_id': 501, 'spent_date': '2026-02-16',
                 'hours': 4.0, 'notes': '', 'project_name': 'P1', 'task_name': 'T1'},
                {'project_id': 200, 'task_id': 502, 'spent_date': '2026-02-17',
                 'hours': 3.0, 'notes': '', 'project_name': 'P2', 'task_name': 'T2'},
            ],
            'user_id': None,
            'created_at': datetime.now(),
            'committed': False,
        }
        # Första lyckas, andra misslyckas
        mock_harvest_client.create_time_entry.side_effect = [
            {'id': 8001},
            RuntimeError("API error"),
        ]

        result = harvest_mcp.harvest_commit_timesheet('partial')
        assert "AVBRUTEN" in result
        assert "8001" in result
        assert "1 poster EJ postade" in result

    def teardown_method(self):
        _drafts.clear()


class TestHarvestUpdateTimeEntry:
    def test_update_hours(self, mock_harvest_client):
        mock_harvest_client.update_time_entry.return_value = {
            'project': {'name': 'Proj A'},
            'hours': 6.0,
        }
        result = harvest_mcp.harvest_update_time_entry(entry_id=1001, hours=6.0)
        assert "Uppdaterad" in result
        assert "1001" in result
        assert "hours" in result

    def test_update_notes(self, mock_harvest_client):
        mock_harvest_client.update_time_entry.return_value = {
            'project': {'name': 'Proj A'},
            'hours': 4.0,
        }
        result = harvest_mcp.harvest_update_time_entry(entry_id=1001, notes="Ny kommentar")
        assert "notes" in result

    def test_no_fields(self, mock_harvest_client):
        result = harvest_mcp.harvest_update_time_entry(entry_id=1001)
        assert "Inga falt" in result
        mock_harvest_client.update_time_entry.assert_not_called()


class TestHarvestDeleteTimeEntry:
    def test_delete(self, mock_harvest_client):
        result = harvest_mcp.harvest_delete_time_entry(entry_id=1001)
        assert "Borttagen" in result
        assert "1001" in result
        mock_harvest_client.delete_time_entry.assert_called_once_with(1001)


# ---------------------------------------------------------------------------
# MCP-serverinstans
# ---------------------------------------------------------------------------

class TestMCPServer:
    def test_instructions_set(self):
        assert "STRATEGI" in harvest_mcp._INSTRUCTIONS
        assert "context" in harvest_mcp._INSTRUCTIONS.lower()

    def test_server_name(self):
        assert harvest_mcp.mcp.name == "HarvestReports"
