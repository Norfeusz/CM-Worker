"""Write helpers for CM360 (test advertiser only; guarded in cm_auth).

Each builder returns its payload in dry-run (default) and only inserts when
dry_run=False AND the service was created with read_only=False. No asset upload:
creatives are simple 1x1 TRACKING_TEXT templates (CM here = tracking/tags only).

Size ids (from live inspection): placement 1x1 = "31"; tracking creative = "255".
"""
import json

TRACKING_CREATIVE_SIZE = "255"   # 0x0 tracking size used by TRACKING_TEXT creatives
PLACEMENT_1x1_SIZE = "31"        # 1x1


def create_site(svc, profile_id, name, url=None, directory_site_id=None, dry_run=True):
    """Add a source/Site to the account, mirroring CM's cascade:
    reuse a Site Directory entry if it exists (by exact name) else create one
    (directorySites.insert), then link it to the account (sites.insert)."""
    if dry_run:
        print(f"[DRY-RUN] create_site '{name}' (dir search/insert + sites.insert, url={url})")
        return {"_dryRun": True, "name": name}
    dsid = directory_site_id
    if not dsid:
        found = svc.directorySites().list(profileId=profile_id, searchString=name,
                                          maxResults=10).execute().get("directorySites", [])
        exact = [d for d in found if (d.get("name") or "").lower() == name.lower()]
        if exact:
            dsid = exact[0]["id"]
        else:
            ds = svc.directorySites().insert(
                profileId=profile_id, body={"name": name, "url": url or ""}).execute()
            dsid = ds["id"]
    return svc.sites().insert(profileId=profile_id,
                              body={"name": name, "directorySiteId": dsid}).execute()


def landing_page(svc, profile_id, advertiser_id, name, url, dry_run=True):
    payload = {"name": name, "url": url, "advertiserId": advertiser_id, "archived": False}
    if dry_run:
        print(f"[DRY-RUN] advertiserLandingPages.insert\n"
              f"{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.advertiserLandingPages().insert(profileId=profile_id, body=payload).execute()


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
