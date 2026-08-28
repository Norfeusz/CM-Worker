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
import datetime
import difflib
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


# Próg podobieństwa DWÓCH członów ścieżki, przy którym uznajemy je za ten sam człon.
# Powód: klient numeruje odsłony tej samej strony (`szkola-2` -> `szkola-8`) i to nadal
# jedna kampania, a dokładne porównanie dawało wtedy zero. Świadomie NIE patrzymy na
# `utm_campaign` — te same wartości wracają w różnych kampaniach, więc jako sygnał
# dopasowania był zdecydowanie zbyt luźny (zgłoszone przez usera).
SEGMENT_MATCH_RATIO = 0.7

# Ile wspólnych członów musi być, żeby kampania dopasowała się AUTOMATYCZNIE. Jeden
# wspólny człon przy dłuższej ścieżce to za mało: `standard/google/1000` i
# `standard/biedronka/other` dzielą tylko `standard`, a to dwie różne kampanie
# (zgłoszone przez usera — dopasowanie bywało za luźne). Przy ścieżce KRÓTSZEJ próg
# schodzi do jej długości, inaczej `szkola-8` ↔ `szkola-2` (jeden człon po anchorze)
# nie dopasowałoby się nigdy.
MIN_SEGMENTS = 2


def _needed(target, rem, minimum=None):
    """Ile członów musi się zgodzić dla TEJ pary ścieżek."""
    return min(MIN_SEGMENTS if minimum is None else minimum, len(target), len(rem))


def _seg_ratio(a, b):
    """Podobieństwo dwóch członów ścieżki, 0..1 (`szkola-8` vs `szkola-2` -> 0.88)."""
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _common_near(a, b, ratio=SEGMENT_MATCH_RATIO):
    """Ile wiodących członów zgadza się DOKŁADNIE albo jest podobnych powyżej progu."""
    c = 0
    for x, y in zip(a, b):
        if x.lower() != y.lower() and _seg_ratio(x, y) < ratio:
            break
        c += 1
    return c


def match_campaigns(url, anchor, campaign_lps, ratio=SEGMENT_MATCH_RATIO,
                    min_segments=MIN_SEGMENTS):
    """campaign_lps: list of {campaignId, campaignName, lpName, lpUrl}.
    Returns (ranked_candidates, suggest_new_campaign:bool).

    Podstawą jest wspólny prefiks członów ścieżki (`common`, dokładny) — tak jak od
    początku. Dodatkiem jest `near`: ten sam prefiks, ale człon może się różnić w
    granicach progu podobieństwa (`szkola-8` ↔ `szkola-2`). Dokładne dopasowanie zawsze
    wygrywa w rankingu; podobieństwo tylko ratuje przypadki, w których jedyny człon
    ścieżki różni się odsłoną.

    Wspólnych członów musi być co najmniej `min_segments` (albo tyle, ile ma krótsza
    ścieżka) — patrz MIN_SEGMENTS. Kandydat, który tego nie spełnia, ZOSTAJE w rankingu
    (UI pokazuje listę do ręcznego wyboru), ale nie jest wybierany automatycznie:
    `enough=False` i `suggest_new=True`.

    `why` mówi, co zadecydowało — trafia do UI, żeby dopasowanie nie było magią i żeby
    było widać, po którym LP kampania się dopasowała.
    """
    target = remaining_path(url, anchor) or []
    by_camp = {}
    for row in campaign_lps:
        rem = remaining_path(row["lpUrl"], anchor) or []
        common = _common_leading(target, rem)
        near = _common_near(target, rem, ratio)
        need = _needed(target, rem, min_segments)
        enough = bool(need) and max(common, near) >= need
        if common:
            why = f"ta sama ścieżka ({'/'.join(rem[:common])})"
        elif near:
            pairs = ", ".join(f"{x} ≈ {y} ({_seg_ratio(x, y):.0%})"
                              for x, y in zip(target[:near], rem[:near]))
            why = f"podobny człon ścieżki: {pairs}"
        else:
            why = "brak wspólnej ścieżki"
        if not enough and (common or near):
            why += (f" — za mało, żeby wybrać automatycznie "
                    f"({max(common, near)} z {need} członów)")
        cand = {"campaignId": row["campaignId"], "campaignName": row["campaignName"],
                "common": common, "near": near, "enough": enough, "needed": need,
                "why": why, "lpName": row.get("lpName"), "lpUrl": row["lpUrl"],
                "lpRemaining": "/".join(rem)}
        cur = by_camp.get(row["campaignId"])
        if not cur or (common, near) > (cur["common"], cur["near"]):
            by_camp[row["campaignId"]] = cand
    ranked = sorted(by_camp.values(),
                    key=lambda c: (c["enough"], c["common"], c["near"]), reverse=True)
    best = ranked[0] if ranked else None
    suggest_new = not best or not best["enough"]
    return ranked, suggest_new


# --- konwencja nazw (JEDNO miejsce, w którym się ją buduje i czyta) -----------
# Nazwa LP: linia{N}-{ŹRÓDŁO}[-{słowo rozróżniające}]  np. linia1-Facebook-lookalike
# Nazwa creative: linia{N}[-{słowo rozróżniające}]      np. linia1-lookalike
#
# Kolejność jest wymogiem, nie estetyką: numer linii, potem źródło, a słowo
# rozróżniające na końcu i tylko gdy istnieje. Wcześniej etykieta stała w środku
# (`linia1-lookalike-Facebook`) i nie dało się odczytać źródła z nazwy — trzeba było
# zgadywać, który segment nim jest, na czym przewracało się wykrywanie konfliktu linii.
LP_NAME_RE = re.compile(r"^\s*linia\s*(\d+)(?:[-_](.*))?$", re.I)


def lp_name(number, source, label=None):
    """Nazwa landing page w obowiązującej konwencji."""
    return f"linia{number}-{source}" + (f"-{label}" if label else "")


def creative_name(number, label=None):
    """Nazwa creative — BEZ źródła; źródło niesie nazwa LP."""
    return f"linia{number}" + (f"-{label}" if label else "")


def split_lp_name(name):
    """(numer, źródło, etykieta) z nazwy LP; None, gdy nazwa nie trzyma konwencji.

    Dzięki temu, że źródło stoi zaraz po numerze, da się je wyłuskać jednoznacznie
    także wtedy, gdy nazwa ma słowo rozróżniające.
    """
    m = LP_NAME_RE.match(name or "")
    if not m:
        return None
    parts = (m.group(2) or "").split("-", 1)
    src = parts[0].strip() or None
    label = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return int(m.group(1)), src, label


# --- konwencja nazw MAILINGU -------------------------------------------------
# Mailing nie ma wymiarów ani „linii": jednostką jest wysyłka, a każdy link w niej to
# osobna strona docelowa i osobna kreacja na JEDNYM adzie. Odwzorowane 1:1 z gotowych
# tagów klienta (Tags_Household 08-12.2026): ad `mail-1`, kreacje `mail-1-CTA`,
# `mail-1-mbank`, `mail-1-regulamin`, `mail-1-slowniczek`, LP `mail1`, `mail1-mbank`…
# Kreska po `mail` jest w nazwie ADA i KREACJI, ale NIE w nazwie LP — tak jest na koncie
# i użytkownik potwierdził, że zostaje.
MAIL_NAME_RE = re.compile(r"^\s*mail\s*-?\s*(\d+)", re.I)
MAIL_LABELS = "abcdefghijklmnopqrstuvwxyz"


def mail_ad_name(number):
    return f"mail-{number}"


def mail_creative_name(number, label):
    return f"mail-{number}-{label}" if label else f"mail-{number}"


def mail_lp_name(number, label):
    return f"mail{number}-{label}" if label else f"mail{number}"


# Miesiące po polsku BEZ znaków diakrytycznych — wartość ląduje w adresie URL.
PL_MONTHS = ["styczen", "luty", "marzec", "kwiecien", "maj", "czerwiec", "lipiec",
             "sierpien", "wrzesien", "pazdziernik", "listopad", "grudzien"]


def utm_campaign_slug(campaign_name, today=None):
    """Wartość `utm_campaign` dla mailingu: nazwa kampanii ucięta PRZED datą, plus
    miesiąc wysyłki słownie (`Household 08-12.2026` -> `household_sierpien`).

    Odtworzone z gotowego arkusza klienta. Sama nazwa kampanii nie wystarcza: niesie
    ZAKRES miesięcy (`08-12.2026`), a w adresie ma stać miesiąc tej konkretnej wysyłki.
    Ucinamy na pierwszym członie z cyfrą, bo to właśnie tam zaczyna się data.

    To tylko wartość domyślna — użytkownik nadpisuje ją w panelu (adres podany ręcznie
    ze swoim `utm_source` nie dostaje już żadnych UTM-ów z szablonu).
    """
    words = []
    for w in re.split(r"[\s_]+", (campaign_name or "").strip()):
        if not w or re.search(r"\d", w):
            break
        words.append(w)
    base = normalize(" ".join(words)) or normalize(campaign_name) or "kampania"
    return f"{base}_{PL_MONTHS[(today or datetime.date.today()).month - 1]}"


def next_mail_number(existing_lps):
    """Kolejny numer wysyłki w kampanii: `mail1` istnieje -> `mail2`.

    Czytane z nazw stron docelowych kampanii, tak samo jak numer linii — numeracja jest
    per kampania, nie per paczka.
    """
    top = 0
    for lp in existing_lps or []:
        m = MAIL_NAME_RE.match(lp.get("lpName") or "")
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def mail_labels(n):
    """Domyślne etykiety linków: a, b, c… (użytkownik zmienia je na `mbank`, `regulamin`).
    Po wyczerpaniu alfabetu numerujemy dalej, żeby nigdy nie powtórzyć etykiety."""
    return [MAIL_LABELS[i] if i < len(MAIL_LABELS) else f"link{i + 1}" for i in range(n)]


def detect_line_conflict(url, anchor, source, existing_lps):
    """Ambiguous case (must ASK): an existing line has the SAME path AND same source
    but a DIFFERENT query (e.g. only `sprzedawca` differs) -> reuse vs add new line."""
    t_path = "/".join(s.lower() for s in (remaining_path(url, anchor) or []))
    t_q = urlparse(url if "//" in url else "//" + url).query
    for lp in existing_lps:
        # źródło to segment ZARAZ po numerze linii, więc nazwa ze słowem
        # rozróżniającym (linia1-Facebook-lookalike) też się poprawnie rozbiera
        parsed = split_lp_name(lp.get("lpName"))
        lp_src = (parsed[1] or "").lower() if parsed else ""
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
        "lpName": lp_name(line_no, source),
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


def keyword_label(text):
    """The label from a keyword the USER typed for a landing page ("lookalike").

    Kept as typed apart from what would break a CM360 name or the naming convention:
    whitespace becomes a single '-', anything that is neither alphanumeric nor '-'/'_'
    is dropped. Deliberately NOT put through `normalize`, which lowercases and strips
    Polish diacritics — those are fine in a name the user chose themselves, and the
    automatic labels are the only ones that need a comparison form.

    Returns None for an empty/garbage keyword, so callers can treat "no keyword" and
    "keyword of punctuation" the same way.
    """
    t = re.sub(r"\s+", "-", (text or "").strip())
    t = "".join(ch for ch in t if ch.isalnum() or ch in "-_").strip("-_")
    return t or None


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


def _by_index(values, n):
    """A per-address list from either a list (short ones are padded) or a dict by index."""
    if isinstance(values, dict):
        return [values.get(i, values.get(str(i))) for i in range(n)]
    values = list(values or [])
    return [values[i] if i < len(values) else None for i in range(n)]


def dedupe_links(urls, keywords=None, sources=None):
    """(unique rows, {index: keyword}, {index: source}) for one order — the shape
    everything downstream is keyed by.

    Must happen BEFORE folder matching and `resolve_lines`, because both key their
    per-landing-page data by position: collapsing a repeated address later would shift
    every keyword and folder label by one and silently label the wrong page.

    A row is (address, source), not just the address: the SAME address under two sources
    is two landing pages (`linia1-GDN` + `linia1-Programmatic`), because the source is
    part of an LP name. A truly repeated row keeps whichever keyword was given for it.
    """
    urls = list(urls or [])
    kws = _by_index(keywords, len(urls))
    srcs = _by_index(sources, len(urls))
    seen = {}
    for i, u in enumerate(urls):
        u = (u or "").strip()
        if not u:
            continue
        kw = kws[i].strip() if isinstance(kws[i], str) else None
        src = srcs[i].strip() if isinstance(srcs[i], str) else None
        key = (u, src)
        seen[key] = seen.get(key) or kw or None
    return ([u for u, _ in seen],
            {i: kw for i, kw in enumerate(seen.values()) if kw},
            {i: src for i, (_, src) in enumerate(seen) if src})


def resolve_lines(urls, anchor, source, existing_lps, labels=None, keywords=None,
                  sources=None):
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
    keywords: optional {index: keyword} typed by the user for that landing page. A
    keyword BEATS every automatic label and is always applied, even for a single link
    with nothing to be distinguished from — the user naming a page is not a guess that
    needs corroborating. It is also the only way to get a short label out of a long
    tracking value (`utm_campaign=refinansowanie2026` -> `refinans`).

    sources: optional {index: source token} — one order may cover SEVERAL sources
    (a package with `GDN/` and `Programmatic/` folders), and the source is part of an LP
    name, so each source needs its own landing page: `linia1-GDN` + `linia1-Programmatic`,
    same number, usually the same page with different tracking parameters. Entries that
    share a number but differ in source need NO label — the source segment already tells
    them apart.

    All three dicts are keyed by position in `urls`, so callers must pass them aligned
    with the list they hand in here (duplicate entries collapse below, which would shift
    positions — dedupe upstream if you build the dicts yourself).
    """
    labels = labels or {}
    keywords = {int(i): keyword_label(k) for i, k in (keywords or {}).items()
                if keyword_label(k)}
    src_by_idx = {int(i): s for i, s in (sources or {}).items() if s}
    # identyczny (adres, źródło) = jedno LP; TEN SAM adres pod DWOMA źródłami to dwa LP
    ent, seen_ent = [], set()
    for i, u in enumerate(urls or []):
        if not u:
            continue
        tok = src_by_idx.get(i, source)
        if (u, tok) in seen_ent:
            continue
        seen_ent.add((u, tok))
        ent.append({"url": u, "src": tok, "label0": labels.get(i),
                    "kw": keywords.get(i)})
    discs = lp_discriminators([e["url"] for e in ent], anchor)
    existing_by_name = {lp.get("lpName"): lp.get("lpUrl", "") or ""
                        for lp in (existing_lps or [])}
    # url -> nazwy LP, jakie już na nim wiszą; wybór między nimi zależy od ŹRÓDŁA wpisu
    existing_by_url = {}
    for lp in (existing_lps or []):
        if lp.get("lpUrl"):
            existing_by_url.setdefault(lp["lpUrl"], []).append(lp.get("lpName"))

    taken, path_no, lines = set(), {}, []
    for i, (e, disc) in enumerate(zip(ent, discs)):
        url, tok = e["url"], e["src"]
        line = resolve_line(url, anchor, tok, existing_lps)
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
        line.update({"lineNumber": no, "url": url, "source": tok,
                     "keyword": e["kw"],
                     "label": e["kw"] or lp_label(disc, e["label0"])})
        lines.append(line)

    # rodzeństwo do rozróżnienia etykietą to wpisy o TYM SAMYM numerze I TYM SAMYM
    # źródle — różne źródła są już rozróżnione segmentem źródła w nazwie
    shared_no = {(l["lineNumber"], l["source"]) for l in lines
                 if sum(1 for x in lines if x["lineNumber"] == l["lineNumber"]
                        and x["source"] == l["source"]) > 1}
    for l in lines:
        no, tok = l["lineNumber"], l["source"]
        # this exact url is already a landing page here: keep its name instead of
        # minting a second LP for the same address under a labelled name. Only an LP of
        # the SAME source counts — pod innym źródłem ten sam adres to inne LP
        # (`linia2-GDN` obok `linia2-Programmatic`), bo źródło jest częścią nazwy.
        same_src = next((n for n in existing_by_url.get(l["url"], [])
                         if ((split_lp_name(n) or (None, None, None))[1] or "").lower()
                         == (tok or "").lower()), None)
        if same_src:
            name = same_src
            l["lpName"] = name
            # A keyword cannot rename it: `_ensure_lp` resolves by NAME, so a renamed
            # landing page would be created as a second LP for the same address. Say so
            # instead of dropping the keyword silently.
            l["keywordIgnored"] = bool(l.get("keyword"))
            # creative bierze etykietę z istniejącej nazwy LP, a nie przez obcięcie
            # źródła z końca — po zmianie kolejności źródło stoi w ŚRODKU
            parsed = split_lp_name(name)
            label = parsed[2] if parsed else None
            l["creativeName"] = creative_name(no, label)
            l["labelled"] = bool(label)
            continue
        # label the LP when siblings share the number, or when the plain name is
        # already taken in the campaign by a DIFFERENT url — `_ensure_lp` resolves
        # by name, so a silent collision would point creatives at the wrong page.
        plain = lp_name(no, tok)
        clash_url = existing_by_name.get(plain)
        collides = clash_url is not None and clash_url != l["url"]
        label = l["label"]
        if collides and not label:
            # Jeden link w zleceniu nie ma rodzeństwa, więc `lp_discriminators` zwraca
            # pustą listę i etykiety nie ma skąd wziąć — ale jest z czym porównać:
            # KOLIDUJĄCE LP. Bez tego nazwa zostawała `linia2-GDN`, `_ensure_lp`
            # znajdował istniejące LP po nazwie i całe zlecenie po cichu dopinało się
            # do cudzej linii, razem z jej creative. Dokładnie ten błąd zgłoszono
            # z żywej sesji (utm_campaign=refinansowanie2026 kontra kampania_gdn).
            toks = lp_discriminators([l["url"], clash_url], anchor)[0]
            # gdy token jest nieczytelny (same cyfry), bierzemy go i tak: brzydka, ale
            # ODRĘBNA nazwa jest lepsza niż cicha kolizja z cudzym LP
            label = lp_label(toks) or next((t for t in toks if t), None)
        # a keyword is an instruction, not a hint: apply it without asking whether the
        # name needs distinguishing (single link, no collision -> still labelled)
        needs = bool(l.get("keyword")) or (((no, tok) in shared_no or collides)
                                           and bool(label))
        l["label"] = label
        l["lpName"] = lp_name(no, tok, label if needs else None)
        l["creativeName"] = creative_name(no, label if needs else None)
        l["labelled"] = needs
    return lines
