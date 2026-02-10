"""
Harvest OAuth2-hantering.

Hanterar initial auktorisering via webbläsare, token-lagring och automatisk refresh.
Token sparas i ~/.harvest/token.json.

Kan köras standalone för initial auth:
    python harvest_auth.py
"""

import os
import json
import time
import logging
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests
import yaml

log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')


def load_config() -> dict:
    """Läs config.yaml."""
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)


def load_token(token_path: str) -> dict | None:
    """Läs token från disk. Returnerar None om filen saknas."""
    path = os.path.expanduser(token_path)
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def save_token(token_path: str, token_data: dict) -> None:
    """Spara token till disk."""
    path = os.path.expanduser(token_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(token_data, f, indent=2)
    log.info(f"Token sparad: {path}")


def is_token_expired(token_data: dict) -> bool:
    """Kolla om access_token har gått ut (med 60s marginal)."""
    expires_at = token_data.get('expires_at', 0)
    return time.time() >= (expires_at - 60)


def refresh_access_token(harvest_config: dict, token_data: dict) -> dict:
    """Använd refresh_token för att hämta nytt access_token.

    HARDFAIL om refresh misslyckas.
    """
    token_url = harvest_config['token_url']

    resp = requests.post(token_url, data={
        'grant_type': 'refresh_token',
        'client_id': harvest_config['client_id'],
        'client_secret': harvest_config['client_secret'],
        'refresh_token': token_data['refresh_token'],
    })

    if resp.status_code != 200:
        raise RuntimeError(
            f"Harvest token refresh failed: {resp.status_code} {resp.text}"
        )

    new_token = resp.json()

    token_data['access_token'] = new_token['access_token']
    token_data['refresh_token'] = new_token.get('refresh_token', token_data['refresh_token'])
    token_data['token_type'] = new_token.get('token_type', 'Bearer')
    token_data['expires_at'] = time.time() + new_token.get('expires_in', 64800)
    token_data['scope'] = new_token.get('scope', token_data.get('scope', ''))

    save_token(harvest_config['token_path'], token_data)
    log.info("Harvest access_token refreshed")
    return token_data


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Enkel HTTP handler som fångar OAuth callback."""

    authorization_code = None
    scope = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == '/callback' and 'code' in params:
            _OAuthCallbackHandler.authorization_code = params['code'][0]
            _OAuthCallbackHandler.scope = params.get('scope', [''])[0]

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(
                b'<html><body><h2>Harvest-autentisering klar!</h2>'
                b'<p>Du kan st\xc3\xa4nga detta f\xc3\xb6nster.</p></body></html>'
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Missing authorization code')

    def log_message(self, format, *args):
        """Tyst - logga inte till stderr."""
        pass


def run_oauth_flow(harvest_config: dict) -> dict:
    """Interaktiv OAuth2-flow via webbläsare.

    1. Starta lokal HTTP-server på port 8080
    2. Öppna webbläsare till authorize_url
    3. Vänta på callback med authorization code
    4. Byt code mot access_token + refresh_token
    5. Hämta account_id
    6. Spara allt
    """
    authorize_params = urlencode({
        'client_id': harvest_config['client_id'],
        'redirect_uri': harvest_config['redirect_uri'],
        'response_type': 'code',
    })
    authorize_url = f"{harvest_config['authorize_url']}?{authorize_params}"

    server = HTTPServer(('localhost', 8080), _OAuthCallbackHandler)
    server.timeout = 120

    print(f"\nOppnar webblasare for Harvest-autentisering...")
    print(f"Om webblasaren inte oppnas, ga till:\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    _OAuthCallbackHandler.authorization_code = None
    while _OAuthCallbackHandler.authorization_code is None:
        server.handle_request()

    server.server_close()
    code = _OAuthCallbackHandler.authorization_code
    log.info("Authorization code mottagen")

    # Byt code mot token
    resp = requests.post(harvest_config['token_url'], data={
        'grant_type': 'authorization_code',
        'client_id': harvest_config['client_id'],
        'client_secret': harvest_config['client_secret'],
        'code': code,
        'redirect_uri': harvest_config['redirect_uri'],
    })

    if resp.status_code != 200:
        raise RuntimeError(
            f"Harvest token exchange failed: {resp.status_code} {resp.text}"
        )

    token_resp = resp.json()

    # Hämta account_id via Harvest ID API
    accounts_resp = requests.get(
        'https://id.getharvest.com/api/v2/accounts',
        headers={'Authorization': f"Bearer {token_resp['access_token']}"}
    )

    if accounts_resp.status_code != 200:
        raise RuntimeError(
            f"Harvest accounts fetch failed: {accounts_resp.status_code} {accounts_resp.text}"
        )

    accounts = accounts_resp.json().get('accounts', [])
    if not accounts:
        raise RuntimeError("Inga Harvest-konton hittades")

    account_id = str(accounts[0]['id'])
    account_name = accounts[0].get('name', 'Unknown')
    log.info(f"Harvest-konto: {account_name} (ID: {account_id})")

    token_data = {
        'access_token': token_resp['access_token'],
        'refresh_token': token_resp['refresh_token'],
        'token_type': token_resp.get('token_type', 'Bearer'),
        'expires_at': time.time() + token_resp.get('expires_in', 64800),
        'scope': _OAuthCallbackHandler.scope or '',
        'account_id': account_id,
        'account_name': account_name,
    }

    save_token(harvest_config['token_path'], token_data)
    print(f"\nAutentisering klar! Konto: {account_name} (ID: {account_id})")
    return token_data


def get_valid_token(harvest_config: dict) -> dict:
    """Huvudfunktion: returnerar alltid en giltig token.

    1. Ladda token från disk
    2. Om utgången: refresh
    3. Om saknas: kör interaktiv OAuth-flow
    """
    token_path = harvest_config.get('token_path', '')
    token_data = load_token(token_path)

    if token_data is not None:
        if is_token_expired(token_data):
            log.info("Harvest token expired, refreshing...")
            token_data = refresh_access_token(harvest_config, token_data)
        return token_data

    log.info("Ingen Harvest-token hittad, startar OAuth-flow...")
    return run_oauth_flow(harvest_config)


# --- Standalone: kör initial auth ---
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    config = load_config()

    # python3 harvest_auth.py forecast  -> forecast-config
    # python3 harvest_auth.py           -> harvest-config (default)
    section = sys.argv[1] if len(sys.argv) > 1 else 'harvest'
    if section not in config:
        print(f"Okand sektion: '{section}'. Tillgangliga: {', '.join(config.keys())}")
        sys.exit(1)

    target_config = config[section]
    print(f"\n--- Autentiserar mot: {section.upper()} ---")
    token = get_valid_token(target_config)
    print(f"\nToken giltig. Account ID: {token['account_id']}")
    print(f"Sparad i: {os.path.expanduser(target_config['token_path'])}")
