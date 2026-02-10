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

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        """HTTP-anrop med auth-refresh och rate limit-hantering.

        - 401 -> refresh token, retry en gång
        - 429 -> respektera Retry-After, vänta, retry
        - Andra fel -> HARDFAIL
        """
        url = f"{self._base_url}{path}" if path.startswith('/') else path

        resp = self._session.request(method, url, params=params)

        # Token expired - refresh och retry
        if resp.status_code == 401:
            log.info("Harvest 401 - refreshing token")
            self._token_data = refresh_access_token(self._config, self._token_data)
            self._session.headers['Authorization'] = f"Bearer {self._token_data['access_token']}"
            resp = self._session.request(method, url, params=params)

        # Rate limited - vänta och retry
        if resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 16))
            log.warning(f"Harvest rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            resp = self._session.request(method, url, params=params)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Harvest API error: {resp.status_code} {method} {url} - {resp.text[:500]}"
            )

        return resp.json()

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


# --- Standalone test ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    config = load_config()
    client = HarvestClient(config['harvest'])

    print("\n--- Users ---")
    users = client.get_users()
    for u in users[:5]:
        cap_h = (u.get('weekly_capacity', 0) or 0) / 3600
        print(f"  {u['first_name']} {u['last_name']} - {cap_h}h/vecka")
    print(f"\nTotalt {len(users)} aktiva anvandare")

    print("\n--- Projects ---")
    projects = client.get_projects()
    for p in projects[:5]:
        client_name = p.get('client', {}).get('name', 'N/A') if p.get('client') else 'N/A'
        print(f"  {p['name']} ({client_name})")
    print(f"\nTotalt {len(projects)} aktiva projekt")
