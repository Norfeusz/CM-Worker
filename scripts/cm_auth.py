"""OAuth2 (installed-app) auth for Campaign Manager 360 API (dfareporting v4).

First run opens a browser for consent and stores credentials/token.json.
Subsequent runs reuse/refresh the token silently.

Run directly to list the user's CM360 profiles (profileId + account).
"""
import json
import os
import re
from urllib.parse import urlparse, parse_qs
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest

import cm_env

SCOPES = ["https://www.googleapis.com/auth/dfatrafficking"]

# ---- SAFETY ALLOWLIST -------------------------------------------------------
# Twarde ograniczenia wymuszane w kodzie, zależne od AKTYWNEGO ŚRODOWISKA
# (`config/environments.json`, wybór zmienną CM_ENV — patrz `cm_env.py`):
#   * tylko profil tego środowiska (test NIE dosięgnie MBanku, prod NIE dosięgnie testu),
#   * tylko advertiserzy tego środowiska — chyba że lista jest pusta (None), co na
#     produkcji znaczy „wszyscy advertiserzy TEGO profilu” (decyzja usera: advertiser
#     bierze się z linku, a profil i tak oddziela konta),
#   * zapisy tylko przy `service(read_only=False)` ORAZ gdy środowisko je dopuszcza,
#   * DELETE nigdy, w żadnym środowisku.
# Każde naruszenie podnosi wyjątek ZANIM żądanie wyjdzie do API.
#
# Funkcje, nie stałe: `CM_ENV` bywa ustawiane przed importem, a testy przełączają
# środowisko w locie. Stałe modułowe zamroziłyby pierwszą odczytaną wartość.


def allowed_profile_ids():
    return {cm_env.profile_id()}


def allowed_advertiser_ids():
    """Zbiór dozwolonych advertiserów albo None = wszyscy tego profilu."""
    return cm_env.advertiser_ids()


def _adv_ok(adv):
    ids = allowed_advertiser_ids()
    return ids is None or str(adv) in ids


def _adv_note():
    ids = allowed_advertiser_ids()
    return "wszyscy tego profilu" if ids is None else sorted(ids)

_ORIG_EXECUTE = HttpRequest.execute
_GUARD_INSTALLED = False
WRITE_ENABLED = False                   # POST/PUT allowed only when True (service(read_only=False))


def _check_body(body):
    """On writes, ensure any advertiserId in the payload stays in the allowlist."""
    if not body:
        return
    try:
        data = json.loads(body) if isinstance(body, (str, bytes)) else body
    except Exception:
        return
    if not isinstance(data, dict):
        return
    adv = data.get("advertiserId")
    if adv is not None and not _adv_ok(adv):
        raise RuntimeError(
            f"SAFETY guard: blocked write with advertiserId={adv} in body "
            f"(allowed: {_adv_note()}).")


def _check_uri(uri):
    """Raise RuntimeError if the URI targets a disallowed profile/advertiser."""
    path = urlparse(uri).path
    qs = parse_qs(urlparse(uri).query)

    # 1) profile in path: /userprofiles/{profileId}/...
    m = re.search(r"/userprofiles/(\d+)", path)
    if m and m.group(1) not in allowed_profile_ids():
        raise RuntimeError(
            f"SAFETY guard: blocked access to profile {m.group(1)} "
            f"(allowed: {sorted(allowed_profile_ids())}). URI={uri}")

    # 2) advertiser get by id: /advertisers/{id}
    m = re.search(r"/advertisers/(\d+)", path)
    if m and not _adv_ok(m.group(1)):
        raise RuntimeError(
            f"SAFETY guard: blocked advertiser {m.group(1)} "
            f"(allowed: {_adv_note()}). URI={uri}")

    # 3) gołe LISTOWANIE advertiserów wyliczyłoby wszystkich na koncie.
    #    Blokowane TYLKO tam, gdzie środowisko ma zamkniętą listę (test): tam każde
    #    wyjście poza jednego advertisera jest błędem. Na produkcji advertiserzy tego
    #    profilu są dozwoleni z definicji, a lista jest potrzebna, żeby w ogóle dało się
    #    zweryfikować mapowanie linku na advertisera. Profil nadal ogranicza regułę 1.
    if re.search(r"/advertisers$", path) and allowed_advertiser_ids() is not None:
        raise RuntimeError(
            "SAFETY guard: blocked listing ALL advertisers. "
            "Scope calls to the allowed advertiser instead. URI=" + uri)

    # 4) any advertiserIds/advertiserId query filter must stay within allowlist
    for key in ("advertiserIds", "advertiserId"):
        for v in qs.get(key, []):
            if not _adv_ok(v):
                raise RuntimeError(
                    f"SAFETY guard: blocked advertiserId filter {v} "
                    f"(allowed: {_adv_note()}). URI={uri}")

    # 5) advertiserId jako SEGMENT ŚCIEŻKI — upload assetów kreacji ma go właśnie tam:
    #    /userprofiles/{profileId}/creativeAssets/{advertiserId}/creativeAssets
    #    Ani reguła 2 (`/advertisers/{id}`), ani 4 (parametr zapytania), ani `_check_body`
    #    (ciało niesie tylko `assetIdentifier`) tego nie widziały, więc upload na obcego
    #    advertisera przechodziłby przez allowlistę. Znalezione przed pierwszym realnym
    #    zapisem programmatica.
    m = re.search(r"/creativeAssets/(\d+)", path)
    if m and not _adv_ok(m.group(1)):
        raise RuntimeError(
            f"SAFETY guard: blocked creative-asset upload for advertiser {m.group(1)} "
            f"(allowed: {_adv_note()}). URI={uri}")


def _install_read_only_guard():
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return

    def _guarded_execute(self, *args, **kwargs):
        method = (getattr(self, "method", "GET") or "GET").upper()
        _check_uri(self.uri)                      # profile/advertiser allowlist (URI)
        if method == "GET":
            return _ORIG_EXECUTE(self, *args, **kwargs)
        if method == "DELETE":
            raise RuntimeError(f"SAFETY guard: DELETE is never allowed. URI={self.uri}")
        if not WRITE_ENABLED:
            raise RuntimeError(
                f"SAFETY guard: blocked {method} to {self.uri}. "
                f"Writes disabled (use service(read_only=False)).")
        _check_body(getattr(self, "body", None))  # advertiserId allowlist (body)
        return _ORIG_EXECUTE(self, *args, **kwargs)

    HttpRequest.execute = _guarded_execute
    _GUARD_INSTALLED = True
# -----------------------------------------------------------------------------
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRED = os.path.join(BASE, "credentials", "client_secret.json")
TOKEN = os.path.join(BASE, "credentials", "token.json")


def get_creds():
    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CRED, SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return creds


def service(read_only=True):
    """Klient CM360 z ZAWSZE założonym bezpiecznikiem.

    Zapis wymaga DWÓCH zgód naraz: wywołania `service(read_only=False)` i środowiska,
    które zapisy dopuszcza. Produkcja startuje jako tylko-do-odczytu (patrz
    `cm_env.writes_allowed`), więc dopóki nie padnie świadome `CM_PROD_WRITES=1`,
    żadna ścieżka w kodzie nie jest w stanie ruszyć konta klienta — nawet ta, która
    o środowisku nic nie wie.
    """
    global WRITE_ENABLED
    _install_read_only_guard()          # guard is ALWAYS installed
    if not read_only:
        ok, why = cm_env.writes_allowed()
        if not ok:
            raise RuntimeError(f"SAFETY guard: {why}")
    WRITE_ENABLED = not read_only       # writes only when explicitly requested
    return build("dfareporting", "v5", credentials=get_creds(), cache_discovery=False)


if __name__ == "__main__":
    svc = service()  # read-only by default
    resp = svc.userProfiles().list().execute()
    items = resp.get("items", [])
    print(f"Found {len(items)} profile(s):")
    for p in items:
        print(f"  profileId={p['profileId']}  accountId={p.get('accountId')}  "
              f"account={p.get('accountName')!r}  user={p.get('userName')!r}")
