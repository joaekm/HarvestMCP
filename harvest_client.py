"""
Harvest API-klient.

Hanterar autentisering, pagination, rate limits och alla API-anrop mot Harvest v2.
Används av harvest_mcp.py för att hämta data on-demand.
"""

import time
import logging

import requests

from harvest_auth import get_valid_token, refresh_access_token, load_config

log = logging.getLogger(__name__)


class HarvestClient:
    """Klient för Harvest API v2.

    Hanterar auth-headers, automatisk pagination och rate limit-respekt.
    """

    def __init__(self, harvest_config: dict):
        self._config = harvest_config
        self._base_url = harvest_config['api_base_url']
        self._session = requests.Session()
        self._ensure_auth()

    def _ensure_auth(self) -> None:
        """Hämta giltig token och sätt session-headers."""
        token_data = get_valid_token(self._config)
        self._token_data = token_data
        self._session.headers.update({
            'Authorization': f"Bearer {token_data['access_token']}",
            'Harvest-Account-Id': token_data['account_id'],
            'User-Agent': self._config.get('user_agent', 'HarvestMCP'),
            'Content-Type': 'application/json',
        })

    def _request(self, method: str, path: str, params: dict = None,
                 json_body: dict = None) -> dict | None:
        """HTTP-anrop med auth-refresh och rate limit-hantering.

        - 401 -> refresh token, retry en gång
        - 429 -> respektera Retry-After, vänta, retry
        - Andra fel -> HARDFAIL
        - 200/201 -> returnera JSON (eller None vid 200 utan body)
        """
        url = f"{self._base_url}{path}" if path.startswith('/') else path

        resp = self._session.request(method, url, params=params, json=json_body)

        # Token expired - refresh och retry
        if resp.status_code == 401:
            log.info("Harvest 401 - refreshing token")
            self._token_data = refresh_access_token(self._config, self._token_data)
            self._session.headers['Authorization'] = f"Bearer {self._token_data['access_token']}"
            resp = self._session.request(method, url, params=params, json=json_body)

        # Rate limited - vänta och retry
        if resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 16))
            log.warning(f"Harvest rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            resp = self._session.request(method, url, params=params, json=json_body)

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Harvest API error: {resp.status_code} {method} {url} - {resp.text[:500]}"
            )

        if resp.content:
            return resp.json()
        return None

    def _paginate(self, path: str, params: dict = None, result_key: str = None) -> list:
        """Hämta alla sidor automatiskt.

        Harvest använder page-baserad pagination med next_page.
        result_key anger vilken nyckel i response som innehåller listan.
        """
        if params is None:
            params = {}
        params.setdefault('per_page', 2000)

        all_results = []
        page = 1

        while True:
            params['page'] = page
            data = self._request('GET', path, params=params)

            # Hitta result_key automatiskt om inte angiven
            if result_key is None:
                for key in data:
                    if isinstance(data[key], list):
                        result_key = key
                        break

            if result_key and result_key in data:
                all_results.extend(data[result_key])

            next_page = data.get('next_page')
            if next_page is None:
                break
            page = next_page

        return all_results

    # ---- Convenience-metoder ----

    def get_users(self, is_active: bool = True) -> list:
        """Hämta alla användare."""
        params = {}
        if is_active:
            params['is_active'] = 'true'
        return self._paginate('/users', params=params, result_key='users')

    def get_projects(self, is_active: bool = True) -> list:
        """Hämta alla projekt."""
        params = {}
        if is_active:
            params['is_active'] = 'true'
        return self._paginate('/projects', params=params, result_key='projects')

    def get_clients(self, is_active: bool = True) -> list:
        """Hämta alla kunder."""
        params = {}
        if is_active:
            params['is_active'] = 'true'
        return self._paginate('/clients', params=params, result_key='clients')

    def get_time_entries(self, from_date: str, to_date: str, **filters) -> list:
        """Hämta tidsposter med datum och valfria filter.

        Filters: user_id, project_id, client_id, is_billed, is_running
        """
        params = {'from': from_date, 'to': to_date}
        for key, value in filters.items():
            if value is not None and value != '':
                params[key] = value
        return self._paginate('/time_entries', params=params, result_key='time_entries')

    def get_report_time_by_team(self, from_date: str, to_date: str) -> list:
        """Hämta aggregerade timmar per teammedlem (Reports API)."""
        params = {'from': from_date, 'to': to_date}
        data = self._request('GET', '/reports/time/team', params=params)
        return data.get('results', [])

    def get_report_time_by_project(self, from_date: str, to_date: str) -> list:
        """Hämta aggregerade timmar per projekt (Reports API)."""
        params = {'from': from_date, 'to': to_date}
        data = self._request('GET', '/reports/time/project', params=params)
        return data.get('results', [])

    def get_report_project_budget(self) -> list:
        """Hämta budget vs actual per projekt (Reports API)."""
        return self._paginate('/reports/project_budget', result_key='results')

    def get_report_uninvoiced(self, from_date: str, to_date: str) -> list:
        """Hämta ofakturerat per kund/projekt (Reports API)."""
        params = {'from': from_date, 'to': to_date}
        data = self._request('GET', '/reports/uninvoiced', params=params)
        return data.get('results', [])

    def get_roles(self) -> list:
        """Hämta alla roller. Kräver administrator-behörighet."""
        return self._paginate('/roles', result_key='roles')

    def get_role(self, role_id: int) -> dict:
        """Hämta en specifik roll med user_ids."""
        return self._request('GET', f'/roles/{role_id}')

    def get_task_assignments(self, project_id: int) -> list:
        """Hämta tillgängliga tasks för ett projekt."""
        return self._paginate(
            f'/projects/{project_id}/task_assignments',
            result_key='task_assignments'
        )

    def create_time_entry(self, project_id: int, spent_date: str, hours: float,
                          task_id: int, notes: str = "", user_id: int = None) -> dict:
        """Skapa ny tidspost. POST /time_entries."""
        body = {
            'project_id': project_id,
            'task_id': task_id,
            'spent_date': spent_date,
            'hours': hours,
        }
        if notes:
            body['notes'] = notes
        if user_id is not None:
            body['user_id'] = user_id
        return self._request('POST', '/time_entries', json_body=body)

    def update_time_entry(self, entry_id: int, **fields) -> dict:
        """Uppdatera tidspost. PATCH /time_entries/{id}.

        Giltiga fields: project_id, task_id, spent_date, hours, notes.
        """
        return self._request('PATCH', f'/time_entries/{entry_id}', json_body=fields)

    def delete_time_entry(self, entry_id: int) -> None:
        """Ta bort tidspost. DELETE /time_entries/{id}."""
        self._request('DELETE', f'/time_entries/{entry_id}')


class ForecastClient:
    """Klient för Forecast API (https://api.forecastapp.com).

    Forecast API har ingen pagination — returnerar allt på en gång.
    Använder Forecast-Account-Id header.
    """

    def __init__(self, forecast_config: dict):
        self._config = forecast_config
        self._base_url = forecast_config['api_base_url']
        self._session = requests.Session()
        self._ensure_auth()

    def _ensure_auth(self) -> None:
        """Hämta giltig token och sätt session-headers."""
        token_data = get_valid_token(self._config)
        self._token_data = token_data
        self._session.headers.update({
            'Authorization': f"Bearer {token_data['access_token']}",
            'Forecast-Account-Id': token_data['account_id'],
            'User-Agent': self._config.get('user_agent', 'HarvestMCP'),
            'Content-Type': 'application/json',
        })

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        """HTTP-anrop med auth-refresh och rate limit-hantering."""
        url = f"{self._base_url}{path}" if path.startswith('/') else path

        resp = self._session.request(method, url, params=params)

        if resp.status_code == 401:
            log.info("Forecast 401 - refreshing token")
            self._token_data = refresh_access_token(self._config, self._token_data)
            self._session.headers['Authorization'] = f"Bearer {self._token_data['access_token']}"
            resp = self._session.request(method, url, params=params)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 16))
            log.warning(f"Forecast rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            resp = self._session.request(method, url, params=params)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Forecast API error: {resp.status_code} {method} {url} - {resp.text[:500]}"
            )

        return resp.json()

    # ---- Convenience-metoder ----

    def get_assignments(self, start_date: str = None, end_date: str = None) -> list:
        """Hämta schemalagda assignments.

        Args:
            start_date: YYYY-MM-DD (default: idag)
            end_date: YYYY-MM-DD (default: 4 veckor fram)
        """
        params = {}
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
        data = self._request('GET', '/assignments', params=params)
        return data.get('assignments', [])

    def get_projects(self) -> list:
        """Hämta alla Forecast-projekt."""
        data = self._request('GET', '/projects')
        return data.get('projects', [])

    def get_people(self) -> list:
        """Hämta alla personer i Forecast."""
        data = self._request('GET', '/people')
        return data.get('people', [])

    def get_clients(self) -> list:
        """Hämta alla kunder i Forecast."""
        data = self._request('GET', '/clients')
        return data.get('clients', [])

    def get_milestones(self) -> list:
        """Hämta alla milstolpar."""
        data = self._request('GET', '/milestones')
        return data.get('milestones', [])

    def get_placeholders(self) -> list:
        """Hämta alla placeholders (ej tillsatta roller)."""
        data = self._request('GET', '/placeholders')
        return data.get('placeholders', [])

    def whoami(self) -> dict:
        """Hämta info om autentiserad användare."""
        return self._request('GET', '/whoami')


# --- Standalone test ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    config = load_config()

    # Harvest
    client = HarvestClient(config['harvest'])

    print("\n--- Harvest Users ---")
    users = client.get_users()
    for u in users[:5]:
        cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
        print(f"  {u['first_name']} {u['last_name']} - {cap_h}h/vecka")
    print(f"\nTotalt {len(users)} aktiva anvandare")

    print("\n--- Harvest Projects ---")
    projects = client.get_projects()
    for p in projects[:5]:
        client_name = p.get('client', {}).get('name', 'N/A') if p.get('client') else 'N/A'
        print(f"  {p['name']} ({client_name})")
    print(f"\nTotalt {len(projects)} aktiva projekt")

    # Forecast (om konfigurerad och autentiserad)
    if 'forecast' in config:
        from harvest_auth import load_token
        fc = config['forecast']
        token = load_token(fc.get('token_path', ''))
        if token:
            print("\n--- Forecast ---")
            fc_client = ForecastClient(fc)
            info = fc_client.whoami()
            print(f"  Inloggad som: {info.get('current_user', {}).get('email', 'unknown')}")
            people = fc_client.get_people()
            print(f"  {len(people)} personer i Forecast")
            fc_projects = fc_client.get_projects()
            print(f"  {len(fc_projects)} projekt i Forecast")
        else:
            print("\n--- Forecast ---")
            print("  Ingen token. Kor: python3 harvest_auth.py forecast")
