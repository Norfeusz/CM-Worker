"""Write helpers for CM360 (test advertiser only; guarded in cm_auth).

Each builder returns its payload in dry-run (default) and only inserts when
dry_run=False AND the service was created with read_only=False. No asset upload:
creatives are simple 1x1 TRACKING_TEXT templates (CM here = tracking/tags only).

Size ids (from live inspection): placement 1x1 = "31"; tracking creative = "255".
"""
import datetime
import json

TRACKING_CREATIVE_SIZE = "255"   # 0x0 tracking size used by TRACKING_TEXT creatives
PLACEMENT_1x1_SIZE = "31"        # 1x1
CAMPAIGN_YEARS = 5               # new campaign length: start + 5 years (user's convention)


def resolve_directory_site(svc, profile_id, name, directory_site_id=None):
    """(directorySiteId|None, human-readable how) for a Site about to be created:
    an explicitly chosen id wins, otherwise an exact (case-insensitive) name match in
    the Site Directory. Read-only, so it is safe to call in dry-run too."""
    if directory_site_id:
        return directory_site_id, "wskazany przez użytkownika"
    if svc is None:
        return None, "bez połączenia z API (nie sprawdzono katalogu)"
    found = svc.directorySites().list(profileId=profile_id, searchString=name,
                                      maxResults=25).execute().get("directorySites", [])
    exact = [d for d in found if (d.get("name") or "").lower() == name.lower()]
    if exact:
        return exact[0]["id"], f"dopasowany po nazwie w Site Directory ({exact[0]['name']})"
    return None, f"brak dokładnego dopasowania w Site Directory ({len(found)} podobnych)"


def create_site(svc, profile_id, name, url=None, directory_site_id=None,
                allow_new_directory_site=False, dry_run=True):
    """Add a source/Site to the account, mirroring CM's "select a site" cascade:
    resolve a Site Directory entry, then link it to the account (sites.insert).

    Creating a brand-new Site Directory entry is gated behind allow_new_directory_site.
    Directory sites are account-wide and cannot be deleted, and this account routinely
    points a Site at a differently-named directory site (Site 'CG_GDN' -> dirSite
    'CG_remarketing'), so matching by name alone would silently mint duplicates.
    Prefer passing a directory_site_id that the user picked from the search results.

    Returns the inserted Site resource, annotated with _directorySiteId and
    _createdDirectorySite; in dry-run a plan dict with the same resolution info.
    """
    dsid, how = resolve_directory_site(svc, profile_id, name, directory_site_id)
    plan = {"_dryRun": True, "name": name, "directorySiteId": dsid, "resolution": how,
            "needsNewDirectorySite": dsid is None}

    if dsid is None and not allow_new_directory_site:
        if dry_run:
            print(f"[DRY-RUN] create_site '{name}': {how} -> wymaga zgody na nowy wpis "
                  f"w Site Directory (url={url or '-'})")
            return plan
        raise RuntimeError(
            f"create_site '{name}': {how}. Wpis w Site Directory jest globalny i "
            f"nieusuwalny — wskaż directory_site_id albo jawnie ustaw "
            f"allow_new_directory_site=True.")

    if dry_run:
        steps = ("sites.insert" if dsid else
                 f"directorySites.insert (url={url or ''}) + sites.insert")
        print(f"[DRY-RUN] create_site '{name}': {how} -> {steps}")
        return plan

    created_ds = False
    if dsid is None:
        ds = svc.directorySites().insert(
            profileId=profile_id, body={"name": name, "url": url or ""}).execute()
        dsid, created_ds = ds["id"], True
    site = svc.sites().insert(profileId=profile_id,
                              body={"name": name, "directorySiteId": dsid}).execute()
    site["_directorySiteId"] = dsid
    site["_createdDirectorySite"] = created_ds
    return site


def landing_page(svc, profile_id, advertiser_id, name, url, dry_run=True):
    payload = {"name": name, "url": url, "advertiserId": advertiser_id, "archived": False}
    if dry_run:
        print(f"[DRY-RUN] advertiserLandingPages.insert\n"
              f"{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.advertiserLandingPages().insert(profileId=profile_id, body=payload).execute()


def campaign_dates(start_date=None, years=CAMPAIGN_YEARS):
    """(startDate, endDate) as YYYY-MM-DD. End = start + N years (project default:
    campaigns are open-ended trackers, so we don't ask for an end date)."""
    start = datetime.date.fromisoformat(start_date) if start_date else datetime.date.today()
    try:
        end = start.replace(year=start.year + years)
    except ValueError:                      # 29.02 in a non-leap target year
        end = start.replace(year=start.year + years, day=28)
    return start.isoformat(), end.isoformat()


def campaign(svc, profile_id, advertiser_id, name, default_lp_id,
             start_date, end_date, dry_run=True):
    """Create a campaign. CM requires a defaultLandingPageId, so the line's landing
    page must already exist — that also registers it in the campaign's LP list."""
    payload = {
        "name": name, "advertiserId": advertiser_id,
        "startDate": start_date, "endDate": end_date,
        "defaultLandingPageId": default_lp_id,
        # standing decision (never ask per campaign): our campaigns are not political
        "euPoliticalAdsDeclaration": "DOES_NOT_CONTAIN_EU_POLITICAL_ADS",
    }
    if dry_run:
        print(f"[DRY-RUN] campaigns.insert\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.campaigns().insert(profileId=profile_id, body=payload).execute()


def creative(svc, profile_id, advertiser_id, name, dry_run=True):
    payload = {"name": name, "advertiserId": advertiser_id, "type": "TRACKING_TEXT",
               "size": {"id": TRACKING_CREATIVE_SIZE}, "active": True}
    if dry_run:
        print(f"[DRY-RUN] creatives.insert\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.creatives().insert(profileId=profile_id, body=payload).execute()


def add_lp_to_campaign(svc, profile_id, campaign_id, lp_id, make_default=False, dry_run=True):
    """Register a landing page in the campaign's landing-page list ("Strony docelowe
    w tej kampanii"). CM exposes only defaultLandingPageId, so a NON-default LP is
    added by briefly setting it as default then restoring the original — the LP
    persists in the campaign list. First line of a new campaign => keep as default."""
    if dry_run:
        how = "as campaign default" if make_default else "via default-cycle (restore orig)"
        print(f"[DRY-RUN] add LP {lp_id} to campaign {campaign_id} ({how})")
        return {"_dryRun": True}
    camp = svc.campaigns().get(profileId=profile_id, id=campaign_id).execute()
    orig = camp.get("defaultLandingPageId")
    svc.campaigns().patch(profileId=profile_id, id=campaign_id,
                          body={"defaultLandingPageId": lp_id}).execute()
    if make_default or not orig:
        return {"default": lp_id}
    svc.campaigns().patch(profileId=profile_id, id=campaign_id,
                          body={"defaultLandingPageId": orig}).execute()
    return {"added": lp_id, "defaultRestored": orig}


def associate_creative_to_campaign(svc, profile_id, campaign_id, creative_id, dry_run=True):
    """Link a creative to a campaign (required before an ad can reference it)."""
    if dry_run:
        print(f"[DRY-RUN] campaignCreativeAssociations.insert creative {creative_id} -> campaign {campaign_id}")
        return {"_dryRun": True}
    existing, req = set(), svc.campaignCreativeAssociations().list(
        profileId=profile_id, campaignId=campaign_id)
    while req is not None:
        resp = req.execute()
        existing.update(a.get("creativeId") for a in
                        resp.get("campaignCreativeAssociations", []))
        req = svc.campaignCreativeAssociations().list_next(req, resp)
    if creative_id in existing:
        return {"_noop": True, "reason": "already associated"}
    return svc.campaignCreativeAssociations().insert(
        profileId=profile_id, campaignId=campaign_id,
        body={"creativeId": creative_id}).execute()


def placement(svc, profile_id, campaign_id, site_id, name,
              start_date, end_date, dry_run=True):
    payload = {
        "name": name, "campaignId": campaign_id, "siteId": site_id,
        "compatibility": "DISPLAY", "paymentSource": "PLACEMENT_AGENCY_PAID",
        "size": {"id": PLACEMENT_1x1_SIZE},
        # 11032: a placement must have >=1 tag format compatible with its type
        "tagFormats": ["PLACEMENT_TAG_JAVASCRIPT", "PLACEMENT_TAG_TRACKING",
                       "PLACEMENT_TAG_TRACKING_IFRAME", "PLACEMENT_TAG_INTERNAL_REDIRECT",
                       "PLACEMENT_TAG_TRACKING_JAVASCRIPT", "PLACEMENT_TAG_CLICK_COMMANDS",
                       "PLACEMENT_TAG_IFRAME_JAVASCRIPT"],
        "pricingSchedule": {
            "startDate": start_date, "endDate": end_date,
            "pricingType": "PRICING_TYPE_CPM",
            "pricingPeriods": [{"startDate": start_date, "endDate": end_date,
                                "units": "0", "rateOrCostNanos": "0"}],
        },
    }
    if dry_run:
        print(f"[DRY-RUN] placements.insert\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.placements().insert(profileId=profile_id, body=payload).execute()


def tracking_ad(svc, profile_id, campaign_id, name, placement_id,
                creative_id, landing_page_id, start_time, end_time, dry_run=True):
    payload = {
        "name": name, "campaignId": campaign_id, "type": "AD_SERVING_TRACKING",
        "active": True, "startTime": start_time, "endTime": end_time,
        "placementAssignments": [{"placementId": placement_id, "active": True}],
        "deliverySchedule": {"priority": "AD_PRIORITY_15", "impressionRatio": "1",
                             "hardCutoff": False},
        "creativeRotation": {"creativeAssignments": [{
            "creativeId": creative_id, "active": True, "applyEventTags": True,
            "clickThroughUrl": {"landingPageId": landing_page_id, "defaultLandingPage": False},
        }]},
    }
    if dry_run:
        print(f"[DRY-RUN] ads.insert\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.ads().insert(profileId=profile_id, body=payload).execute()


def append_creative_to_ad(svc, profile_id, ad_id, creative_id, landing_page_id, dry_run=True):
    """Add a creative assignment (a new line) to an EXISTING tracking ad."""
    assignment = {
        "creativeId": creative_id, "active": True, "applyEventTags": True,
        "clickThroughUrl": {"landingPageId": landing_page_id, "defaultLandingPage": False},
    }
    if dry_run:
        print(f"[DRY-RUN] ads.update -> append creative {creative_id} "
              f"(clickThrough LP {landing_page_id}) to ad {ad_id}")
        return {"_dryRun": True, "adId": ad_id, "assignment": assignment}
    ad = svc.ads().get(profileId=profile_id, id=ad_id).execute()
    assigns = ad.setdefault("creativeRotation", {}).setdefault("creativeAssignments", [])
    if any(ca.get("creativeId") == creative_id for ca in assigns):
        return {"_noop": True, "adId": ad_id}
    assigns.append(assignment)
    return svc.ads().update(profileId=profile_id, body=ad).execute()
