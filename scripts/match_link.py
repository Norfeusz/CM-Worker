"""Link -> campaign matcher (live wrapper around scripts/matcher.py).

Resolves the advertiser from config/advertiser_map.json (prototype; AI fallback
is pluggable), then reads the advertiser's campaigns + landing pages from CM360
and ranks campaigns by remaining-path match. Read-only.

Usage:
  py scripts/match_link.py "<url>"
  py scripts/match_link.py --json "<url>"
"""
import json
import os
import sys
import matcher as M
import cm_env
from cm_auth import service

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(BASE, "config", "advertiser_map.json")

# --test routes any resolved advertiser's campaign lookup to the test account,
# while keeping the real anchor for path-stripping. Lets us validate campaign
# matching with production-style mbank URLs against the test advertiser.
#
# Nazwy zostają (18 wywołań w `serve.py` i pół orkiestratora), ale WARTOŚCI idą teraz
# z aktywnego środowiska — patrz `cm_env`. Na produkcji `TEST_ADVERTISER` jest None,
# bo advertiserów jest kilkanaście i wybiera go LINK; kod, który go potrzebuje, musi
# wtedy użyć `advertiser_for()`, a nie zakładać, że istnieje jeden.
TEST_PROFILE = cm_env.profile_id()
_env_advertisers = cm_env.advertiser_ids()
TEST_ADVERTISER = sorted(_env_advertisers)[0] if _env_advertisers else None


def advertiser_for(rule):
    """Advertiser, na którym mamy pracować dla TEGO linku.

    Na koncie testowym jest jeden i wygrywa zawsze — dzięki temu produkcyjne adresy
    mBanku da się przepuścić przez testowego advertisera (to cały sens trybu `--test`).
    Na produkcji bierzemy tego, którego `advertiser_map.json` rozwiązał z linku; brak
    reguły to błąd do pokazania użytkownikowi, nie cichy fallback na cokolwiek.
    """
    if TEST_ADVERTISER:
        return TEST_ADVERTISER
    adv = (rule or {}).get("advertiserId")
    if not adv:
        raise RuntimeError(
            "Nie rozpoznano advertisera dla tego adresu, a na produkcji nie ma "
            "domyślnego. Uzupełnij regułę w config/advertiser_map.json.")
    return adv


def _fetch_campaign_lps(svc, profile_id, advertiser_id):
    """Return [{campaignId, campaignName, lpName, lpUrl}] for the advertiser."""
    rows, camps, req = [], [], svc.campaigns().list(
        profileId=profile_id, advertiserIds=[advertiser_id], sortField="NAME")
    while req is not None:
        resp = req.execute()
        camps.extend(resp.get("campaigns", []))
        req = svc.campaigns().list_next(req, resp)

    for c in camps:
        lreq = svc.advertiserLandingPages().list(
            profileId=profile_id, campaignIds=[c["id"]])
        while lreq is not None:
            lresp = lreq.execute()
            for lp in lresp.get("landingPages", []):
                rows.append({"campaignId": c["id"], "campaignName": c["name"],
                             "lpName": lp.get("name"), "lpUrl": lp.get("url", "")})
            lreq = svc.advertiserLandingPages().list_next(lreq, lresp)
    return rows


def run(url, as_json=False, test_mode=False):
    rules = json.load(open(MAP_PATH, encoding="utf-8"))["rules"]
    rule = M.resolve_advertiser(url, rules)
    out = {"url": url, "advertiser": rule}
    if not rule:
        out["error"] = "no advertiser rule matched -> AI fallback would run here"
        print(json.dumps(out, indent=2, ensure_ascii=False)); return

    anchor = rule.get("anchor", [])
    profile_id = TEST_PROFILE if test_mode else rule["profileId"]
    advertiser_id = TEST_ADVERTISER if test_mode else rule["advertiserId"]
    svc = service(read_only=True)
    lps = _fetch_campaign_lps(svc, profile_id, advertiser_id)
    ranked, suggest_new = M.match_campaigns(url, anchor, lps)
    out.update({"suggestNewCampaign": suggest_new, "candidates": ranked})

    if as_json:
        print(json.dumps(out, indent=2, ensure_ascii=False)); return
    print(f"URL:        {url}")
    print(f"advertiser: {rule['advertiser']} (id={rule['advertiserId']})")
    print(f"remaining:  {'/'.join(M.remaining_path(url, anchor) or [])!r}")
    if suggest_new:
        print(">> no campaign shares the path -> SUGGEST NEW CAMPAIGN")
    print("campaign candidates (by remaining-path match):")
    for r in ranked[:8]:
        print(f"  common={r['common']}  {r['campaignName']}  (id={r['campaignId']})"
              f"  <- LP {r['lpName']!r} [{r['lpRemaining']}]")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__); sys.exit(1)
    run(args[0], as_json="--json" in sys.argv, test_mode="--test" in sys.argv)
