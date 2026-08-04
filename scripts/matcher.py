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
from urllib.parse import parse_qs, urlparse

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"gclid", "dclid", "fbclid", "gad_source", "gad_campaignid",
                 "gbraid", "wbraid", "msclkid", "kampania", "option", "sprzedawca"}
# The source is already encoded in the LP name suffix (linia3-GDN), so a difference
# here is the WEAKEST label candidate — kept for folder matching, ranked last.
SOURCE_KEYS = {"utm_source"}
PL_CHARS = str.maketrans("ąćęłńóśźż", "acelnoszz")
MIN_TOKEN = 3          # shorter tokens match too many folder names by accident


def normalize(text):
    """Comparison form for folder names and URL tokens: lowercase, no Polish
    diacritics, every run of other characters collapsed to a single '_'."""
    t = (text or "").lower().translate(PL_CHARS)
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


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


# --- several landing pages in ONE order (same campaign) -----------------------

def _query(url):
    return parse_qs(urlparse(url if "//" in url else "//" + url).query)


def lp_discriminators(urls, anchor):
    """Per URL, the normalized tokens that tell it apart from the OTHER urls.

    Two deterministic signals: path segments (after the advertiser anchor) that are
    not shared by every url, and values of query keys whose value is not the same
    everywhere. A single url has nothing to be distinguished from, so it gets [].

    This is the one signal reused twice downstream — to label landing pages
    (`linia3-prospecting-GDN`) and to decide which zip folder belongs to which LP.
    """
    if not urls:
        return []
    paths = [remaining_path(u, anchor) or [] for u in urls]
    queries = [_query(u) for u in urls]

    shared = set.intersection(*[{s.lower() for s in p} for p in paths]) if paths else set()
    keys = set().union(*[set(q) for q in queries]) if queries else set()
    varying = {k for k in keys if len({tuple(q.get(k, [])) for q in queries}) > 1}
    # strongest signal first: path, then ordinary query keys, then the source key
    ranked = sorted(varying - SOURCE_KEYS) + sorted(varying & SOURCE_KEYS)

    out = []
    for p, q in zip(paths, queries):
        toks = [normalize(s) for s in p if s.lower() not in shared]
        for k in ranked:
            toks += [normalize(v) for v in q.get(k, [])]
        seen_t, uniq = set(), []
        for t in toks:
            if t and t not in seen_t:
                seen_t.add(t)
                uniq.append(t)
        out.append(uniq)
    return out


def _readable(token):
    """Worth putting in an LP/creative name? A bare number or a hash-like id
    (utm_content=12345, utm_content=a3f9c081) names nothing a human recognises."""
    return bool(token) and not token.isdigit() and not re.fullmatch(r"[0-9a-f]{8,}", token)


def lp_label(tokens, fallback=None):
    """The label for a landing page variant: first human-readable discriminator
    token, else the fallback (normally a matched zip folder name), else None."""
    return next((t for t in tokens if _readable(t)), None) or (
        normalize(fallback) if fallback and _readable(normalize(fallback)) else None)


def _words(text):
    """Significant words of a name, for comparing a folder against a URL token."""
    return {w for w in normalize(text).split("_") if len(w) >= MIN_TOKEN}


def match_folders_to_lps(folders, discriminators):
    """Assign each top-level zip folder to the landing page it carries materials for.

    Two ways to match, because a folder and a URL rarely agree on form:
      * whole-name containment either way — folder `Prospecting` vs
        `utm_medium=prospecting`, folder `remarketing_gdn` vs `remarketing`;
      * a shared WORD — folder `SPÓŁKA GIF` vs path segment `konto-spolka`. Neither
        name contains the other, yet they plainly refer to the same page; folder names
        routinely combine the product with the file format.
    Words and tokens shorter than MIN_TOKEN are ignored — they hit unrelated folder
    names by accident.

    Returns {"map": {folder: lpIndex}, "ambiguous": [...], "unmatched": [...]}.
    `ambiguous` and `unmatched` are escalated to the user/AI instead of guessed: an
    abbreviation like `FRC` for `firmootwieracz` is not derivable, and `KONTO FIRMOWE`
    genuinely fits both `.../konto/` and `.../konto-spolka/`.
    """
    out, ambiguous, unmatched = {}, [], []
    for f in folders:
        nf, fw = normalize(f), _words(f)
        hits = []
        for i, toks in enumerate(discriminators):
            usable = [t for t in toks if len(t) >= MIN_TOKEN]
            contained = any(t in nf or nf in t for t in usable)
            shared_word = any(fw & _words(t) for t in usable)
            if contained or shared_word:
                hits.append(i)
        if len(hits) == 1:
            out[f] = hits[0]
        elif hits:
            ambiguous.append({"folder": f, "candidates": hits})
        else:
            unmatched.append(f)
    return {"map": out, "ambiguous": ambiguous, "unmatched": unmatched}


def unresolved_lp_folders(folder_match, n_lines):
    """Zip folders whose landing page could not be decided AND that are worth asking
    about. Two distinct situations:

    * `ambiguous` — the folder name fits several landing pages. Always ask; guessing
      here silently tags materials for the wrong page.
    * `unmatched` — asked about ONLY when some other folder did match a landing page.
      Then the zip is evidently organised per page and a leftover sibling is
      suspicious. If nothing matched at all the zip simply isn't organised that way
      (`GIF/`, `HTML/`, `300x250/`…) and every folder feeds every line — the ordinary
      "same graphics under both pages" order, which needs no question.
    """
    fm = folder_match or {}
    if n_lines < 2:
        return []
    out = [{"folder": a["folder"], "candidates": a.get("candidates")}
           for a in fm.get("ambiguous") or []]
    if fm.get("map"):
        out += [{"folder": f, "candidates": None} for f in fm.get("unmatched") or []]
    return out


def resolve_lines(urls, anchor, source, existing_lps, labels=None):
    """Resolve SEVERAL links added to the same campaign in one order.

    `resolve_line` cannot be called in a plain loop for this: it derives the next
    free number as `max_no + 1` from the landing pages ALREADY in the campaign, so
    two brand-new links would both claim the same number. Numbers claimed earlier in
    this order are tracked here instead.

    Links sharing a destination path are VARIANTS of one line (the prospecting /
    remarketing case: same page, different `utm_medium`) and share its number; their
    LP names get a label to stay distinct. Links with different paths become
    separate lines.

    labels: optional {index: fallback_label} used when a url carries no readable
    discriminator of its own (e.g. the name of the zip folder matched to it).
    """
    urls = list(dict.fromkeys(u for u in urls if u))       # identical links = one LP
    discs = lp_discriminators(urls, anchor)
    labels = labels or {}
    existing_by_name = {lp.get("lpName"): lp.get("lpUrl", "") or ""
                        for lp in (existing_lps or [])}
    existing_by_url = {lp.get("lpUrl"): lp.get("lpName")
                       for lp in (existing_lps or []) if lp.get("lpUrl")}

    taken, path_no, lines = set(), {}, []
    for i, (url, disc) in enumerate(zip(urls, discs)):
        line = resolve_line(url, anchor, source, existing_lps)
        key = (line["path"] or "").lower()
        if line["reused"]:
            no = line["lineNumber"]
        elif key in path_no:
            no = path_no[key]                              # same path in this order
        else:
            no = line["lineNumber"]
            while no in taken:                             # a new line took it already
                no += 1
        taken.add(no)
        path_no.setdefault(key, no)
        line.update({"lineNumber": no, "url": url,
                     "label": lp_label(disc, labels.get(i))})
        lines.append(line)

    shared_no = {l["lineNumber"] for l in lines
                 if sum(1 for x in lines if x["lineNumber"] == l["lineNumber"]) > 1}
    for l in lines:
        base = f"linia{l['lineNumber']}"
        # this exact url is already a landing page here: keep its name instead of
        # minting a second LP for the same address under a labelled name
        if l["url"] in existing_by_url:
            name = existing_by_url[l["url"]]
            l["lpName"] = name
            l["creativeName"] = re.sub(rf"[-_]{re.escape(source)}$", "", name, flags=re.I)
            l["labelled"] = l["creativeName"] != base
            continue
        # label the LP when siblings share the number, or when the plain name is
        # already taken in the campaign by a DIFFERENT url — `_ensure_lp` resolves
        # by name, so a silent collision would point creatives at the wrong page.
        collides = (existing_by_name.get(f"{base}-{source}", l["url"]) != l["url"])
        needs = l["lineNumber"] in shared_no or collides
        l["lpName"] = (f"{base}-{l['label']}-{source}" if needs and l["label"]
                       else f"{base}-{source}")
        l["creativeName"] = f"{base}-{l['label']}" if needs and l["label"] else base
        l["labelled"] = needs and bool(l["label"])
    return lines
