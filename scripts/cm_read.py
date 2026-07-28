"""Read-only helpers: resolve live CM360 state needed to plan writes.

fetch_state() returns id-maps + existing structure for a campaign so the write
planner can decide REUSE(id) vs CREATE for every node. All GETs (guard-safe).
"""


def _paginate(coll, key, **params):
    out, req = [], coll().list(**params)
    while req is not None:
        resp = req.execute()
        out.extend(resp.get(key, []))
        req = coll().list_next(req, resp)
    return out


def search_sites(svc, profile_id, q, limit=25):
    """Mirror CM's site picker cascade: Current account first, then Site Directory."""
    ql = (q or "").lower()
    acct = [{"id": s["id"], "name": s["name"], "directorySiteId": s.get("directorySiteId")}
            for s in _paginate(svc.sites, "sites", profileId=profile_id)
            if ql in s["name"].lower()][:limit]
    dr = svc.directorySites().list(profileId=profile_id, searchString=q,
                                   maxResults=limit).execute().get("directorySites", [])
    directory = [{"id": x["id"], "name": x.get("name"), "url": x.get("url")} for x in dr]
    return {"account": acct, "directory": directory}


def existing_tree(state):
    """{site: {placement: {ad: [creativeName,...]}}} view of fetch_state — the
    canonical shape build_proposal's `existing=` param and the orchestrator expect."""
    tree = {}
    for (sn, pn) in state["placements"]:
        tree.setdefault(sn, {}).setdefault(pn, {})
    for (sn, pn, an), cres in state["ad_creatives"].items():
        tree.setdefault(sn, {}).setdefault(pn, {})[an] = sorted(cres)
    return tree


def site_structure(state, site):
    """Picker-friendly view scoped to ONE site: {placements:[{name, ads:[{name, creatives}]}]}.
    Powers the UI's "add existing placement/ad" pickers."""
    tree = existing_tree(state).get(site, {})
    placements = [{"name": pn, "ads": [{"name": an, "creatives": cres}
                  for an, cres in sorted(ads.items())]}
                  for pn, ads in sorted(tree.items())]
    return {"placements": placements}


def fetch_state(svc, profile_id, advertiser_id, campaign_id=None):
    sites = _paginate(svc.sites, "sites", profileId=profile_id)
    sites_by_name = {s["name"]: s for s in sites}

    # advertiser-level landing pages (to reuse by name+url and avoid duplicates)
    adv_lps = _paginate(svc.advertiserLandingPages, "landingPages",
                        profileId=profile_id, advertiserIds=[advertiser_id])
    state = {
        "sites_by_name": {n: s["id"] for n, s in sites_by_name.items()},
        "placements": {},      # (siteName, placementName) -> placementId
        "ads": {},             # (siteName, placementName, adName) -> adId
        "ad_creatives": {},    # (siteName, placementName, adName) -> set(creativeName)
        "creatives_by_name": {},  # creativeName -> creativeId
        "lps_by_name": {},     # lpName -> landingPageId (within campaign)
        "adv_lp_by_name_url": {(lp["name"], lp.get("url", "")): lp["id"] for lp in adv_lps},
    }
    if not campaign_id:
        return state

    site_name_by_id = {s["id"]: n for n, s in sites_by_name.items()}
    placements = _paginate(svc.placements, "placements",
                           profileId=profile_id, campaignIds=[campaign_id])
    plc_by_id = {p["id"]: p for p in placements}
    for p in placements:
        sn = site_name_by_id.get(p.get("siteId"), f"site#{p.get('siteId')}")
        state["placements"][(sn, p["name"])] = p["id"]

    creatives = _paginate(svc.creatives, "creatives",
                          profileId=profile_id, campaignId=campaign_id)
    state["creatives_by_name"] = {c["name"]: c["id"] for c in creatives}
    cre_name_by_id = {c["id"]: c["name"] for c in creatives}

    ads = _paginate(svc.ads, "ads", profileId=profile_id, campaignIds=[campaign_id])
    for a in ads:
        cre_names = {cre_name_by_id.get(ca.get("creativeId"))
                     for ca in (a.get("creativeRotation", {}) or {}).get("creativeAssignments", []) or []}
        cre_names.discard(None)
        for pa in a.get("placementAssignments", []) or []:
            p = plc_by_id.get(pa.get("placementId"))
            if not p:
                continue
            sn = site_name_by_id.get(p.get("siteId"), f"site#{p.get('siteId')}")
            k = (sn, p["name"], a["name"])
            state["ads"][k] = a["id"]
            state["ad_creatives"].setdefault(k, set()).update(cre_names)

    lps = _paginate(svc.advertiserLandingPages, "landingPages",
                    profileId=profile_id, campaignIds=[campaign_id])
    state["lps_by_name"] = {lp["name"]: lp["id"] for lp in lps}
    return state
