"""Pure (no-API) matching core for link -> advertiser -> campaign -> line.

Kept API-free so it can be unit-tested against real example URLs. The live
wrapper (match_link.py) feeds it landing pages read from CM360.

Rules (confirmed with user):
 * advertiser  = URL segments (prototype; ignores /lp2/2026/c1/-style prefixes)
 * campaign    = compare remaining path AFTER advertiser segments (utm ignored);
                 shared leading segment => same campaign; none => suggest new
 * line number = tied to the destination PATH within a campaign; source suffix
                 (GDN/FB/...) comes from the UI. Same path => same line number.
"""
import re
from urllib.parse import urlparse

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"gclid", "dclid", "fbclid", "gad_source", "gad_campaignid",
                 "gbraid", "wbraid", "msclkid", "kampania", "option", "sprzedawca"}


def canonical(url):
    """(host, segments) with scheme/www/query/fragment/trailing-slash removed."""
    if "//" not in url:
        url = "//" + url
    u = urlparse(url)
    host = (u.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    segs = [s for s in u.path.strip("/").split("/") if s]
    return host, segs


def _find_anchor(segs, anchor):
    """Index just past a consecutive anchor subsequence in segs, else -1."""
    if not anchor:
        return 0
    n = len(anchor)
    for i in range(len(segs) - n + 1):
        if [s.lower() for s in segs[i:i + n]] == [a.lower() for a in anchor]:
            return i + n
    return -1


def remaining_path(url, anchor):
    """Path segments after the advertiser anchor (the part that distinguishes
    campaigns/lines). Returns None if the anchor is not present."""
    _, segs = canonical(url)
    idx = _find_anchor(segs, anchor)
    if idx < 0:
        return None
    return segs[idx:]


def resolve_advertiser(url, rules):
    """Longest-anchor match wins. Rule may use 'anchor' (segments) or 'host'."""
    host, segs = canonical(url)
    best, best_len = None, -1
    for r in rules:
        if r.get("host"):
            if host == r["host"].lower() and best_len < 0:
                best, best_len = r, 0
            continue
        anchor = r.get("anchor", [])
        if _find_anchor(segs, anchor) >= 0 and len(anchor) > best_len:
            best, best_len = r, len(anchor)
    return best


def _common_leading(a, b):
    c = 0
    for x, y in zip(a, b):
        if x.lower() == y.lower():
            c += 1
        else:
            break
    return c


def match_campaigns(url, anchor, campaign_lps):
    """campaign_lps: list of {campaignId, campaignName, lpName, lpUrl}.
    Returns (ranked_candidates, suggest_new_campaign:bool)."""
    target = remaining_path(url, anchor) or []
    by_camp = {}
    for row in campaign_lps:
        rem = remaining_path(row["lpUrl"], anchor) or []
        common = _common_leading(target, rem)
        cur = by_camp.get(row["campaignId"])
        cand = {"campaignId": row["campaignId"], "campaignName": row["campaignName"],
                "common": common, "lpName": row.get("lpName"), "lpUrl": row["lpUrl"],
                "lpRemaining": "/".join(rem)}
        if not cur or common > cur["common"]:
            by_camp[row["campaignId"]] = cand
    ranked = sorted(by_camp.values(), key=lambda c: c["common"], reverse=True)
    suggest_new = not ranked or ranked[0]["common"] == 0
    return ranked, suggest_new


def detect_line_conflict(url, anchor, source, existing_lps):
    """Ambiguous case (must ASK): an existing line has the SAME path AND same source
    but a DIFFERENT query (e.g. only `sprzedawca` differs) -> reuse vs add new line."""
    t_path = "/".join(s.lower() for s in (remaining_path(url, anchor) or []))
    t_q = urlparse(url if "//" in url else "//" + url).query
    for lp in existing_lps:
        m = re.search(r"linia\s*\d+[-_]?(.*)$", lp.get("lpName", "") or "", re.I)
        lp_src = (m.group(1) if m else "").strip().lower()
        lp_url = lp.get("lpUrl", "") or ""
        lp_path = "/".join(s.lower() for s in (remaining_path(lp_url, anchor) or []))
        lp_q = urlparse(lp_url if "//" in lp_url else "//" + lp_url).query
        if lp_path == t_path and lp_src == source.lower() and lp_q != t_q:
            return {"conflict": True, "existingLpName": lp.get("lpName"),
                    "existingUrl": lp_url, "newUrl": url}
    return {"conflict": False}


def resolve_line(url, anchor, source, existing_lps):
    """Decide the line for a URL being added to a chosen campaign.
    existing_lps: list of {lpName, lpUrl}. Returns dict describing the line."""
    target = remaining_path(url, anchor) or []
    target_key = "/".join(s.lower() for s in target)

    seen = {}  # lineNo -> path key
    max_no = 0
    for lp in existing_lps:
        m = re.search(r"linia\s*(\d+)", lp.get("lpName", ""), re.I)
        if not m:
            continue
        no = int(m.group(1))
        max_no = max(max_no, no)
        rem = remaining_path(lp["lpUrl"], anchor) or []
        seen.setdefault(no, "/".join(s.lower() for s in rem))

    reused = next((no for no, k in seen.items() if k == target_key), None)
    line_no = reused if reused is not None else max_no + 1
    return {
        "lineNumber": line_no,
        "reused": reused is not None,
        "source": source,
        "lpName": f"linia{line_no}-{source}",
        "path": "/".join(target),
    }
