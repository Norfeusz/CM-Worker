"""Read-only CM360 browser. Validates the Site->Placement->Ad->Creative model
against live data. Every call is a GET (enforced by the read-only guard in cm_auth).

Usage:
  py scripts/cm_tree.py profiles
  py scripts/cm_tree.py advertisers <profileId>
  py scripts/cm_tree.py campaigns   <profileId> [advertiserId]
  py scripts/cm_tree.py lps         <profileId> <campaignId>
  py scripts/cm_tree.py tree        <profileId> <campaignId>
"""
import sys
from cm_auth import service, ALLOWED_ADVERTISER_IDS

DEFAULT_ADVERTISER = sorted(ALLOWED_ADVERTISER_IDS)[0]


def _all(request_fn, list_fn, key, **params):
    """Paginate a CM360 list endpoint, returning all items."""
    out, req = [], list_fn(**params)
    while req is not None:
        resp = req.execute()
        out.extend(resp.get(key, []))
        req = request_fn().list_next(req, resp)
    return out


def profiles(svc):
    for p in svc.userProfiles().list().execute().get("items", []):
        print(f"{p['profileId']:>10}  acct={p.get('accountId'):>8}  {p.get('accountName')!r}")


def advertisers(svc, pid):
    # Listing all advertisers is blocked by the safety guard; show the allowed one.
    a = svc.advertisers().get(profileId=pid, id=DEFAULT_ADVERTISER).execute()
    print(f"  {a['id']:>10}  {a['name']}  (status={a.get('status')})")


def campaigns(svc, pid, advertiser_id=None):
    advertiser_id = advertiser_id or DEFAULT_ADVERTISER
    params = dict(profileId=pid, sortField="NAME", advertiserIds=[advertiser_id])
    items = _all(lambda: svc.campaigns(), svc.campaigns().list, "campaigns", **params)
    print(f"{len(items)} campaigns:")
    for c in items:
        print(f"  {c['id']:>10}  adv={c.get('advertiserId'):>10}  "
              f"defaultLP={c.get('defaultLandingPageId')}  {c['name']}")


def lps(svc, pid, campaign_id):
    items = _all(lambda: svc.advertiserLandingPages(), svc.advertiserLandingPages().list,
                 "landingPages", profileId=pid, campaignIds=[campaign_id])
    print(f"{len(items)} landing pages for campaign {campaign_id}:")
    for lp in items:
        print(f"  {lp['id']:>10}  {lp['name']!r}\n             {lp.get('url')}")


def tree(svc, pid, campaign_id):
    sites = {s["id"]: s["name"] for s in
             _all(lambda: svc.sites(), svc.sites().list, "sites", profileId=pid)}
    plc = {p["id"]: p for p in
           _all(lambda: svc.placements(), svc.placements().list,
                "placements", profileId=pid, campaignIds=[campaign_id])}
    ads = _all(lambda: svc.ads(), svc.ads().list, "ads",
               profileId=pid, campaignIds=[campaign_id])
    cr = {c["id"]: c["name"] for c in
          _all(lambda: svc.creatives(), svc.creatives().list,
               "creatives", profileId=pid, campaignId=campaign_id)}

    # group: site -> placement -> [ (adName, [creativeNames]) ]
    grouped = {}
    for ad in ads:
        for pa in ad.get("placementAssignments", []) or []:
            p = plc.get(pa.get("placementId"))
            if not p:
                continue
            site = sites.get(p.get("siteId"), f"site#{p.get('siteId')}")
            pname = p.get("name")
            crenames = []
            for ca in (ad.get("creativeRotation", {}) or {}).get("creativeAssignments", []) or []:
                crenames.append(cr.get(ca.get("creativeId"), f"cr#{ca.get('creativeId')}"))
            grouped.setdefault(site, {}).setdefault(pname, []).append((ad.get("name"), crenames))

    print(f"Campaign {campaign_id}: {len(ads)} ads, {len(plc)} placements\n")
    for site in sorted(grouped):
        print(f"[SITE] {site}")
        for pname in sorted(grouped[site]):
            print(f"   (Placement) {pname}")
            for adname, crens in grouped[site][pname]:
                print(f"      Ad: {adname:<28} -> Creative: {', '.join(crens) or '(none)'}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd, rest = sys.argv[1], sys.argv[2:]
    svc = service(read_only=True)
    {
        "profiles": lambda: profiles(svc),
        "advertisers": lambda: advertisers(svc, rest[0]),
        "campaigns": lambda: campaigns(svc, *rest),
        "lps": lambda: lps(svc, rest[0], rest[1]),
        "tree": lambda: tree(svc, rest[0], rest[1]),
    }[cmd]()
