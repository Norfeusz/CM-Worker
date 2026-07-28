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

SCOPES = ["https://www.googleapis.com/auth/dfatrafficking"]

# ---- SAFETY ALLOWLIST -------------------------------------------------------
# Hard limits enforced in code until we explicitly go to production:
#   * only these CM360 user profiles may be queried (blocks the MBank profile),
#   * only these advertisers may be touched (blocks all other advertisers),
#   * only GET requests are allowed (blocks every write).
# Any violation raises before the request reaches the API.
ALLOWED_PROFILE_IDS = {"9556074"}       # Cube Group EMEA CEE PL (test)
ALLOWED_ADVERTISER_IDS = {"11992166"}   # test advertiser in Cube Group

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
    if adv is not None and str(adv) not in ALLOWED_ADVERTISER_IDS:
        raise RuntimeError(
            f"SAFETY guard: blocked write with advertiserId={adv} in body "
            f"(allowed: {sorted(ALLOWED_ADVERTISER_IDS)}).")


def _check_uri(uri):
    """Raise RuntimeError if the URI targets a disallowed profile/advertiser."""
    path = urlparse(uri).path
    qs = parse_qs(urlparse(uri).query)

    # 1) profile in path: /userprofiles/{profileId}/...
    m = re.search(r"/userprofiles/(\d+)", path)
    if m and m.group(1) not in ALLOWED_PROFILE_IDS:
        raise RuntimeError(
            f"SAFETY guard: blocked access to profile {m.group(1)} "
            f"(allowed: {sorted(ALLOWED_PROFILE_IDS)}). URI={uri}")

    # 2) advertiser get by id: /advertisers/{id}
    m = re.search(r"/advertisers/(\d+)", path)
    if m and m.group(1) not in ALLOWED_ADVERTISER_IDS:
        raise RuntimeError(
            f"SAFETY guard: blocked advertiser {m.group(1)} "
            f"(allowed: {sorted(ALLOWED_ADVERTISER_IDS)}). URI={uri}")

    # 3) bare advertiser LIST (no id) would enumerate all advertisers -> block
    if re.search(r"/advertisers$", path):
        raise RuntimeError(
            "SAFETY guard: blocked listing ALL advertisers. "
            "Scope calls to the allowed advertiser instead. URI=" + uri)

    # 4) any advertiserIds/advertiserId query filter must stay within allowlist
    for key in ("advertiserIds", "advertiserId"):
        for v in qs.get(key, []):
            if v not in ALLOWED_ADVERTISER_IDS:
                raise RuntimeError(
                    f"SAFETY guard: blocked advertiserId filter {v} "
                    f"(allowed: {sorted(ALLOWED_ADVERTISER_IDS)}). URI={uri}")


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
    global WRITE_ENABLED
    _install_read_only_guard()          # guard is ALWAYS installed
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
