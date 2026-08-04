"""Generate CM360 tracking tags and export them to a .xls matching CG's format.

Exports the DELTA — only the given (ad, creative) pairs (the new/edited lines) — as
a "Tracking Ads" workbook with the same columns as CG's Tags_* exports. A pair list
(not a single creative) supports ads that carry MULTIPLE creatives (e.g. linia4-słońce
+ linia4-niebo on the same dimension).

Usage:
  py scripts/export_tags.py <campaignId> <creativeId> <adId>[,<adId>...] [out.xls]
"""
import datetime
import os
import re
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


# --- wygląd 1:1 z eksportem CM360 -------------------------------------------
# Wartości odczytane z prawdziwego pliku wygenerowanego przez CM360
# (Tags_Junior_2026_CG Indywidualny - Konta.xls), nie zgadnięte:
#   * Arial 8pt w całym arkuszu (my mieliśmy 10pt)
#   * nagłówki sekcji i tabeli na tle #99CCFF (paleta 44 = pale_blue), z ramkami
#   * etykiety w bloku CONTRACT INFORMATION pogrubione
#   * zawijanie tekstu w wierszach danych (tagi są długie)
#   * blok nagłówkowy scalony: etykieta c1..c7, wartość c8..c12
#   * zamrożony podział pod wierszem nagłówka, żeby nazwy kolumn zostały widoczne
BASE = "font: name Arial, height 160;"                       # 160 = 8pt
BORDER = "borders: left thin, right thin, top thin, bottom thin;"
FILL = "pattern: pattern solid, fore_colour pale_blue;"

SHEET_NAME = "Tracking Ads"
NOTE_TITLE = "Trafficking Instructions/Notes"
# CM360 wpisuje "1.0" w r1c8, ale scala CAŁY wiersz 1 (c1..c12) w jedną komórkę, więc
# Excel pokazuje tylko "CONTRACT INFORMATION" — ta wersja jest w pliku NIEWIDOCZNA.
# Odwzorowujemy to, co widać, i dlatego jej nie zapisujemy.
# Kolumny, którym CM daje białe wypełnienie (reszta bez wypełnienia). Rozkład jest
# nieregularny — tak to robi eksporter CM; na białym arkuszu nie widać różnicy, ale
# trzymamy się pliku wzorcowego, żeby porównanie bajt-w-bajt nie rozjeżdżało się bez
# powodu.
WHITE_FILL_COLS = set(range(1, 11)) | {13, 15}
# Boilerplate CM360 — zostaje dosłownie, bo to instrukcja dla wydawcy (cache-busting,
# dc_rdid, obowiązek raportowania nielegalnych treści w EOG), a celem jest zgodność
# z plikiem, który traffickerzy i wydawcy już znają.
NOTE_BODY = (
    "This workbook contains code required for implementing tracking ads. The code may "
    "not be valid HTML and should be implemented as specified by your ad server. Please "
    "see https://support.google.com/dcm/partner/answer/2837435 to learn more.\n\n"
    "To ensure proper cache-busting, replace [timestamp] with a dynamically generated "
    "random number. Learn more at "
    "https://support.google.com/dcm/partner/answer/2837435.\n\n"
    "When copying tags from this spreadsheet, be sure to click inside the cell and "
    "highlight the text you want to copy. If you select and copy the entire cell, rather "
    "than the text within it, some applications may put an extra set of quotation marks "
    "(\"\") around the tags, causing them to function incorrectly when they're placed on "
    "the publisher's webpage.\n\n"
    "The publisher needs to insert device IDs into dc_rdid to enable in-app conversion "
    "tracking. Learn more at "
    "https://support.google.com/dcm/partner/answer/2826636#mobile.\n\n"
    "The publisher can designate its playback method for each ad by using the dc_vpm "
    "parameter. Learn more at https://support.google.com/dcm/answer/2826636#10.\n\n"
    "In the European Economic Area (EEA), your ad must include a mechanism for users to "
    "report illegal content and there may also be requirements to surface additional "
    "transparency information about your ad. The publisher and/or buying platform must "
    "notify Google of any illegal content reports using the appropriate form at "
    "https://support.google.com/legal/troubleshooter/1114905#ts=2981967%2C2982031%2C12980091."
)
# Szerokości kolumn w jednostkach BIFF, skopiowane SUROWO z pliku CM360. Świadomie nie
# liczymy ich jako znaki × 256: CM zapisuje wartości typu 2669 czy 3401, których tak nie
# odtworzysz, a różnica jest widoczna przy porównaniu obu plików obok siebie.
# Indeks 0 to wąska kolumna marginesu po lewej.
COL_WIDTHS = [950, 2669, 3401, 3401, 3401, 3401, 5997, 3401, 5997, 3620, 2048, 2121,
              2121, 2669, 6034, 2669, 6034, 11739, 11739, 11739, 11739, 11739]
ROW_H_SECTION, ROW_H_NOTE, ROW_H_DATA, ROW_H_BLANK = 315, 2299, 867, 255
DEFAULT_ROW_HEIGHT = 300
FIRST_DATA_ROW = 11


def _safe(part):
    """Nazwa pliku: znaki zabronione w Windows na podkreślenie, bez zbitek."""
    out = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(part or "")).strip(" ._")
    return re.sub(r"_{2,}", "_", out) or "brak"


def tags_filename(campaign, adv, when=None):
    """Tags_kampania_advertiser_data.xls — schemat wymagany przez użytkownika.

    Data jest w ISO (RRRR-MM-DD), żeby pliki z jednej kampanii sortowały się
    chronologicznie i nie było wątpliwości dzień/miesiąc.
    """
    day = (when or datetime.date.today()).isoformat()
    return f"Tags_{_safe(campaign.get('name'))}_{_safe(adv.get('name'))}_{day}.xls"


def write_xls(campaign, adv, rows, path):
    wb = xlwt.Workbook(encoding="utf-8")
    sh = wb.add_sheet(SHEET_NAME)

    st_section = xlwt.easyxf(f"{BASE} font: bold on, colour black; {FILL} {BORDER}")
    st_label = xlwt.easyxf(f"{BASE} font: bold on; {BORDER}")
    st_value = xlwt.easyxf(f"{BASE} {BORDER}")
    st_note = xlwt.easyxf(f"{BASE} {BORDER} alignment: wrap on, vert top, horiz left;")
    st_header = xlwt.easyxf(f"{BASE} font: bold on; {FILL} {BORDER}")
    wrap = "alignment: wrap on, vert top, horiz left;"
    st_data = xlwt.easyxf(f"{BASE} {BORDER} {wrap}")
    st_data_white = xlwt.easyxf(
        f"{BASE} {BORDER} {wrap} pattern: pattern solid, fore_colour white;")

    for c, w in enumerate(COL_WIDTHS):
        sh.col(c).width = w
    sh.row_default_height = DEFAULT_ROW_HEIGHT

    def h(row, height):
        sh.row(row).height_mismatch = True
        sh.row(row).height = height

    h(1, ROW_H_SECTION)
    sh.write_merge(1, 1, 1, 12, "CONTRACT INFORMATION", st_section)
    for i, (label, value) in enumerate([
            ("Advertiser ID", adv["id"]), ("Advertiser Name", adv["name"]),
            ("Campaign ID", campaign["id"]), ("Campaign Name", campaign["name"])]):
        h(2 + i, ROW_H_SECTION)
        sh.write_merge(2 + i, 2 + i, 1, 7, label, st_label)
        sh.write_merge(2 + i, 2 + i, 8, 12, value, st_value)

    h(6, ROW_H_BLANK)                       # CM ustawia wysokość także pustym wierszom
    h(9, ROW_H_BLANK)
    h(7, ROW_H_SECTION)
    sh.write_merge(7, 7, 1, 12, NOTE_TITLE, st_section)
    h(8, ROW_H_NOTE)
    sh.write_merge(8, 8, 1, 12, NOTE_BODY, st_note)

    h(10, ROW_H_SECTION)
    for c, head in enumerate(HEADERS):
        sh.write(10, c + 1, head, st_header)
    for i, row in enumerate(rows):
        h(FIRST_DATA_ROW + i, ROW_H_DATA)
        for c, val in enumerate(row):
            col = c + 1
            sh.write(FIRST_DATA_ROW + i, col, val,
                     st_data_white if col in WHITE_FILL_COLS else st_data)

    # nazwy kolumn zostają widoczne przy przewijaniu — przy 100+ tagach to różnica
    # między czytelnym arkuszem a zgadywaniem, która kolumna to który tag
    sh.set_panes_frozen(True)
    sh.set_horz_split_pos(FIRST_DATA_ROW)
    sh.set_remove_splits(True)
    wb.save(path)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)
    cid, creative_id, ad_ids = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    svc = service(read_only=False)  # generatetags is a POST
    pairs = [(aid, creative_id) for aid in ad_ids]
    campaign, adv, rows = collect_rows(svc, "9556074", cid, pairs)
    out = sys.argv[4] if len(sys.argv) > 4 else os.path.join(
        os.path.dirname(__file__), "..", "data", tags_filename(campaign, adv))
    write_xls(campaign, adv, rows, out)
    print(f"wrote {len(rows)} tag row(s) -> {out}")
    for r in rows:
        print(f"  {r[7]:10} ad={r[13]:10} creative={r[15]:8} imp(img)={r[16][:70]}...")
