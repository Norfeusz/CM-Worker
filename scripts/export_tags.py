"""Generate CM360 tracking tags and export them to a .xls matching CG's format.

Exports the DELTA — only the given (ad, creative) pairs (the new/edited lines) — as
a "Tracking Ads" workbook with the same columns as CG's Tags_* exports. A pair list
(not a single creative) supports ads that carry MULTIPLE creatives (e.g. linia4-słońce
+ linia4-niebo on the same dimension).

Usage:
  py scripts/export_tags.py <campaignId> <creativeId> <adId>[,<adId>...] [out.xls]
"""
import os
import sys

import xlwt
from cm_auth import service
from cm_read import _paginate

TAG_FORMATS = ["PLACEMENT_TAG_TRACKING", "PLACEMENT_TAG_TRACKING_IFRAME",
               "PLACEMENT_TAG_TRACKING_JAVASCRIPT"]
IMP_BY_FORMAT = {"PLACEMENT_TAG_TRACKING": "img",
                 "PLACEMENT_TAG_TRACKING_IFRAME": "iframe",
                 "PLACEMENT_TAG_TRACKING_JAVASCRIPT": "js"}

HEADERS = ["Advertiser ID", "Advertiser Name", "Campaign ID", "Campaign Name",
           "Placement ID", "Placement External ID", "Site", "Placement Name",
           "Placement Compatibility", "Dimensions", "Start Date", "End Date",
           "Ad ID", "Ad Name", "Creative ID", "Creative Name",
           "Impression Tag (image)", "Impression Tag (iframe)",
           "Impression Tag (JavaScript)", "Third-party vendor tracking tag", "Click Tag"]


def collect_rows(svc, profile_id, campaign_id, pairs):
    """pairs: iterable of (adId, creativeId) tuples — the exact delta to export."""
    pairs = list(pairs)
    wanted = set(pairs)
    ad_ids = {p[0] for p in pairs}
    creative_ids = {p[1] for p in pairs}
    campaign = svc.campaigns().get(profileId=profile_id, id=campaign_id).execute()
    adv = svc.advertisers().get(profileId=profile_id, id=campaign["advertiserId"]).execute()
    creatives = {c["id"]: c for c in _paginate(svc.creatives, "creatives", profileId=profile_id,
                 campaignId=campaign_id) if c["id"] in creative_ids}

    ads = {a["id"]: a for a in _paginate(svc.ads, "ads", profileId=profile_id,
                                         campaignIds=[campaign_id]) if a["id"] in ad_ids}
    placement_ids = {pa["placementId"] for a in ads.values()
                     for pa in a.get("placementAssignments", []) or []}
    placements = {p["id"]: p for p in _paginate(svc.placements, "placements",
                  profileId=profile_id, campaignIds=[campaign_id]) if p["id"] in placement_ids}
    sites = {s["id"]: s["name"] for s in _paginate(svc.sites, "sites", profileId=profile_id)}

    # tags: {(placementId, adId, creativeId): {img, iframe, js, click}}
    tags = {}
    resp = svc.placements().generatetags(
        profileId=profile_id, campaignId=campaign_id,
        placementIds=list(placement_ids), tagFormats=TAG_FORMATS).execute()
    for pt in resp.get("placementTags", []):
        pid = pt.get("placementId")
        for td in pt.get("tagDatas", []):
            key = (td.get("adId"), td.get("creativeId"))
            if key not in wanted:
                continue
            slot = tags.setdefault((pid, td["adId"], td["creativeId"]), {})
            kind = IMP_BY_FORMAT.get(td.get("format"))
            if td.get("impressionTag"):
                slot[kind] = td["impressionTag"]
            if td.get("clickTag"):
                slot["click"] = td["clickTag"]

    rows = []
    for (pid, adid, creid), t in tags.items():
        a, p, creative = ads[adid], placements[pid], creatives[creid]
        size = p.get("size", {})
        rows.append([
            adv["id"], adv["name"], campaign["id"], campaign["name"],
            pid, p.get("externalId", ""), sites.get(p.get("siteId"), ""),
            p.get("name"), (p.get("compatibility") or "").capitalize(),
            f'{size.get("width")}x{size.get("height")}',
            campaign.get("startDate"), campaign.get("endDate"),
            adid, a.get("name"), creid, creative.get("name"),
            t.get("img", ""), t.get("iframe", ""), t.get("js", ""), "", t.get("click", ""),
        ])
    rows.sort(key=lambda r: (str(r[13]), str(r[15])))
    return campaign, adv, rows


def write_xls(campaign, adv, rows, path):
    wb = xlwt.Workbook(encoding="utf-8")
    sh = wb.add_sheet("Tracking Ads")
    bold = xlwt.easyxf("font: bold on")
    sh.write(1, 1, "CONTRACT INFORMATION", bold)
    sh.write(2, 1, "Advertiser ID"); sh.write(2, 8, adv["id"])
    sh.write(3, 1, "Advertiser Name"); sh.write(3, 8, adv["name"])
    sh.write(4, 1, "Campaign ID"); sh.write(4, 8, campaign["id"])
    sh.write(5, 1, "Campaign Name"); sh.write(5, 8, campaign["name"])
    for c, h in enumerate(HEADERS):
        sh.write(10, c + 1, h, bold)
    for i, row in enumerate(rows):
        for c, val in enumerate(row):
            sh.write(11 + i, c + 1, val)
    wb.save(path)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)
    cid, creative_id, ad_ids = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    out = sys.argv[4] if len(sys.argv) > 4 else os.path.join(
        os.path.dirname(__file__), "..", "data", f"Tags_delta_{cid}.xls")
    svc = service(read_only=False)  # generatetags is a POST
    pairs = [(aid, creative_id) for aid in ad_ids]
    campaign, adv, rows = collect_rows(svc, "9556074", cid, pairs)
    write_xls(campaign, adv, rows, out)
    print(f"wrote {len(rows)} tag row(s) -> {out}")
    for r in rows:
        print(f"  {r[7]:10} ad={r[13]:10} creative={r[15]:8} imp(img)={r[16][:70]}...")
