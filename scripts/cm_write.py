"""Write helpers for CM360 (test advertiser only; guarded in cm_auth).

Each builder returns its payload in dry-run (default) and only inserts when
dry_run=False AND the service was created with read_only=False. No asset upload:
creatives are simple 1x1 TRACKING_TEXT templates (CM here = tracking/tags only).

Size ids (from live inspection): placement 1x1 = "31"; tracking creative = "255".
"""
import datetime
import json
import os

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


# --- PROGRAMMATIC (źródło SERWUJĄCE) — CM realnie hostuje kreacje ------------
# Inny model niż tracking: prawdziwy asset idzie na serwer CM, kreacja nazywa się
# wymiarem, placement deklaruje listę wymiarów, a jeden ad `AD_SERVING_STANDARD_AD`
# niesie wszystkie kreacje placementu. Kształty wzięte z discovery v5, nie z pamięci.

# CreativeAssetId.type; z discovery: IMAGE / FLASH / VIDEO / HTML / HTML_IMAGE / AUDIO
ASSET_TYPE_BY_EXT = {".zip": "HTML", ".html": "HTML", ".htm": "HTML",
                     ".png": "IMAGE", ".jpg": "IMAGE", ".jpeg": "IMAGE", ".gif": "IMAGE",
                     ".mp4": "VIDEO", ".mov": "VIDEO"}
MIME_BY_EXT = {".zip": "application/zip", ".html": "text/html", ".htm": "text/html",
               ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime"}
SERVING_YEARS = 3                # placement serwujący: koniec = start + 3 lata


def _ext(filename):
    return os.path.splitext(filename or "")[1].lower()


def asset_kind(filename):
    """(CreativeAssetId.type, mime) dla pliku materiału."""
    e = _ext(filename)
    return ASSET_TYPE_BY_EXT.get(e, "IMAGE"), MIME_BY_EXT.get(e, "application/octet-stream")


def size_of(dimension):
    """`300x250` -> {"width": 300, "height": 250}. CM dopasuje albo utworzy wpis Size."""
    w, _, h = (dimension or "").lower().partition("x")
    return {"width": int(w), "height": int(h)}


def creative_asset(svc, profile_id, advertiser_id, filename, data, dry_run=True):
    """Wgraj JEDEN plik materiału i zwróć jego `assetIdentifier`.

    Upload MUSI być nie-resumable: bezpiecznik `cm_auth` siedzi na `HttpRequest.execute`,
    a upload resumable leci przez `next_chunk()` i ominąłby go. Discovery v5 zresztą
    wystawia dla `creativeAssets.insert` **wyłącznie** protokół `simple` (limit 1 GB),
    więc innej drogi i tak nie ma.

    CM może zmienić nazwę assetu przy kolizji, dlatego identyfikator bierzemy z ODPOWIEDZI,
    nigdy z tego, co wysłaliśmy.
    """
    atype, mime = asset_kind(filename)
    meta = {"assetIdentifier": {"name": os.path.basename(filename), "type": atype}}
    if dry_run:
        print(f"[DRY-RUN] creativeAssets.insert {filename} ({atype}, {len(data or b'')} B)")
        return {"_dryRun": True, "assetIdentifier": meta["assetIdentifier"]}
    import io as _io
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(_io.BytesIO(data), mimetype=mime, resumable=False)
    r = svc.creativeAssets().insert(profileId=profile_id, advertiserId=advertiser_id,
                                    body=meta, media_body=media).execute()
    return {"assetIdentifier": r.get("assetIdentifier") or meta["assetIdentifier"],
            "clickTags": r.get("clickTags") or [], "raw": r}


def display_creative(svc, profile_id, advertiser_id, name, dimension, asset_id,
                     click_tags=None, backup_asset_id=None, dry_run=True):
    """Kreacja SERWOWANA (HTML5/obraz) z wgranym assetem w roli PRIMARY.

    `asset_id` to `assetIdentifier` zwrócony przez `creative_asset()`.
    `click_tags` przepisujemy z odpowiedzi uploadu: CM wykrywa je w zipie HTML5 i bez
    nich kreacja nie ma gdzie kliknąć.

    Rozstrzygnięte pierwszym realnym insertem (15.09.2026, konto testowe):
      * typ to `DISPLAY` — potwierdzone też odczytem 45 kreacji z produkcji;
      * asset `BACKUP_IMAGE` NIE jest wymagany: produkcyjne kreacje mają sam `PRIMARY`
        i ŻADNEGO pola `backupImage*`;
      * **każdy clickTag musi mieć `eventName`** — inaczej CM odrzuca insert błędem
        `8169 : Nazwa raportowania jest wymagana.` Komunikat myli, bo brzmi jak brak
        `backupImageReportingLabel`; dodanie tamtego pola daje dopiero `8248 : Kreacje
        bez obrazu zapasowego nie mogą używać ustawień kreacji zapasowej`. Winowajca
        wskazany testem izolującym: `windowMode` i `active` na asecie są niewinne,
        wystarczy brak `eventName`. A `creativeAssets.insert` zwraca clickTagi WŁAŚNIE
        bez `eventName`, więc przepisując je trzeba to pole uzupełnić z `name`.
    """
    assets = [{"assetIdentifier": asset_id, "role": "PRIMARY", "active": True}]
    if backup_asset_id:
        assets.append({"assetIdentifier": backup_asset_id, "role": "BACKUP_IMAGE",
                       "active": True})
    payload = {"name": name, "advertiserId": advertiser_id, "type": "DISPLAY",
               "size": size_of(dimension), "active": True, "creativeAssets": assets}
    if backup_asset_id:
        payload["backupImageReportingLabel"] = name
    if click_tags:
        payload["clickTags"] = [dict(t, eventName=t.get("eventName") or t.get("name"))
                                for t in click_tags]
    if dry_run:
        print(f"[DRY-RUN] creatives.insert (DISPLAY)\n"
              f"{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.creatives().insert(profileId=profile_id, body=payload).execute()


def serving_placement(svc, profile_id, campaign_id, site_id, name, sizes,
                      start_date, end_date=None, dry_run=True):
    """Placement, na którym CM SERWUJE: deklaruje listę wymiarów, nie 1x1.

    Pierwszy wymiar idzie w `size`, reszta w `additionalSizes` — to z nich CM tworzy
    sobie ady `{wymiar} Default Web Ad` i bierze dla nich DOMYŚLNĄ stronę docelową
    kampanii (dlatego LP audiencji `-default` musi zostać defaultem kampanii).
    """
    dims = list(sizes) or ["1x1"]
    end_date = end_date or campaign_dates(start_date, SERVING_YEARS)[1]
    payload = {
        "name": name, "campaignId": campaign_id, "siteId": site_id,
        "compatibility": "DISPLAY", "paymentSource": "PLACEMENT_AGENCY_PAID",
        "size": size_of(dims[0]),
        "additionalSizes": [size_of(d) for d in dims[1:]],
        "tagFormats": ["PLACEMENT_TAG_STANDARD", "PLACEMENT_TAG_JAVASCRIPT",
                       "PLACEMENT_TAG_IFRAME_JAVASCRIPT", "PLACEMENT_TAG_INTERNAL_REDIRECT"],
        "pricingSchedule": {
            "startDate": start_date, "endDate": end_date,
            "pricingType": "PRICING_TYPE_CPM",
            "pricingPeriods": [{"startDate": start_date, "endDate": end_date,
                                "units": "0", "rateOrCostNanos": "0"}],
        },
    }
    if dry_run:
        print(f"[DRY-RUN] placements.insert (serwujący, {len(dims)} wymiarów)\n"
              f"{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"_dryRun": True, "payload": payload}
    return svc.placements().insert(profileId=profile_id, body=payload).execute()


def standard_ad(svc, profile_id, campaign_id, name, placement_id, creative_ids,
                landing_page_id, start_time, end_time, dry_run=True):
    """Ad SERWUJĄCY (`AD_SERVING_STANDARD_AD`) ze WSZYSTKIMI kreacjami placementu.

    Rotacja równa i losowa — przy jednej kreacji pole i tak nie szkodzi, a przy wielu
    CM bez niego nie wie, jak je ważyć.
    """
    payload = {
        "name": name, "campaignId": campaign_id, "type": "AD_SERVING_STANDARD_AD",
        "active": True, "startTime": start_time, "endTime": end_time,
        "placementAssignments": [{"placementId": placement_id, "active": True}],
        "deliverySchedule": {"priority": "AD_PRIORITY_15", "impressionRatio": "1",
                             "hardCutoff": False},
        "creativeRotation": {
            "type": "CREATIVE_ROTATION_TYPE_RANDOM",
            "weightCalculationStrategy": "WEIGHT_STRATEGY_EQUAL",
            "creativeAssignments": [{
                "creativeId": cid, "active": True, "applyEventTags": True,
                "clickThroughUrl": {"landingPageId": landing_page_id,
                                    "defaultLandingPage": False},
            } for cid in creative_ids],
        },
    }
    if dry_run:
        print(f"[DRY-RUN] ads.insert (serwujący, {len(creative_ids)} kreacji)\n"
              f"{json.dumps(payload, ensure_ascii=False, indent=2)}")
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
