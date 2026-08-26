"""Proposal builder: merge parsed zip + matched campaign/line + source convention
(+ optional existing campaign structure) into an editable Site->Placement->Ad->
Creative tree. Pure/testable; the UI edits this contract and write-back consumes it.

Usage (demo, offline):
  py scripts/build_proposal.py <zip> <source> <lineNumber> [--json]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import matcher


def _group_lines(campaign_lps):
    """Group a campaign's landing pages into lines: [{number, variants:[{lpName,url}]}]."""
    lines = {}
    for lp in campaign_lps or []:
        m = re.search(r"linia\s*(\d+)", lp.get("lpName", "") or "", re.I)
        if not m:
            continue
        no = int(m.group(1))
        lines.setdefault(no, []).append(
            {"lpName": lp.get("lpName"), "url": lp.get("lpUrl", "")})
    return [{"number": n, "variants": v} for n, v in sorted(lines.items())]

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_MAP = os.path.join(BASE, "config", "source_map.json")


def lp_source(source, source_map=None):
    """The token that stands for the source INSIDE a landing page name (`linia3-FB`).

    Separate from the source key itself, because the account uses short forms in names
    (`linia1-FB`, `linia2-GDN`) while the config keys are spelled out (`Facebook`,
    `Meta` — two keys, one Site, one name token). Keeping them apart also fixes
    `detect_line_conflict`, which reads the source back OUT of an existing LP name and
    compared `Facebook` with `FB` — a comparison that could never match.
    """
    source_map = source_map or json.load(open(SRC_MAP, encoding="utf-8"))["sources"]
    return (source_map.get(source) or {}).get("lpSource") or source


def _ad_name(unit, ad_key):
    if ad_key == "variant":
        return unit.get("variant") or unit.get("dimension") or "?"
    if ad_key == "variant_dim_card":
        parts = [unit.get("variant"), unit.get("dimension"), unit.get("card_index")]
        return "_".join(str(p) for p in parts if p) or "?"
    return unit.get("dimension") or "?"          # default: dimension


def _status(name, container):
    """existing if name is a key/member of container (a dict or set), else new."""
    if container is None:
        return "new"
    return "existing" if name in container else "new"


def build_questions(parsed, line_conflict=None, chosen_source=None, folder_match=None,
                    lines=None, source_map=None, sources=None, line_addresses=None):
    """Decision points to surface in the UI before/while building the tree."""
    q = []
    src_conf = ((source_map or json.load(open(SRC_MAP, encoding="utf-8"))["sources"])
                .get(chosen_source) or {})
    selected = selected_sources(chosen_source, sources) if chosen_source else []
    lns = lines or []
    # Pytamy o ADRESY, nie o wpisy linii: przy kilku źródłach jeden adres ma wpis na
    # każde źródło, a odpowiedź „folder -> ta strona” dotyczy adresu (tak też keyowany
    # jest `folderMap` po stronie serwera).
    addr_of = list(line_addresses or range(len(lns)))
    addr_first = {}
    for j, a in enumerate(addr_of):
        addr_first.setdefault(a, j)
    consumed = set((folder_match or {}).get("consumed") or [])
    for prob in matcher.unresolved_lp_folders(folder_match, len(addr_first)):
        f = prob["folder"]
        # przy kilku źródłach nazwa LP nie identyfikuje adresu (jest ich kilka na adres),
        # więc etykietą jest wtedy sam adres
        per_source = len(addr_first) != len(lns)
        opts = [{"value": str(a),
                 "label": (f"{lns[j]['creativeName']} → {lns[j]['url']}" if per_source
                           else f"{lns[j]['creativeName']} → {lns[j]['lpName']}")}
                for a, j in sorted(addr_first.items())]
        opts.append({"value": "all", "label": "wszystkie LP (te same materiały pod każdą stronę)"})
        q.append({
            "id": f"lp_folder:{f}", "type": "single_choice", "rebuild": True,
            "prompt": f"Do której strony docelowej należą materiały z folderu „{f}”?",
            "options": opts, "default": "all",
            "note": ("Nazwa folderu pasuje do kilku LP — nie zgadujemy."
                     if prob["candidates"] else
                     "Pozostałe foldery udało się przypisać do LP, tego nie — sprawdź."),
        })
    # A folder that names a FORMAT of the chosen source (Meta: `karuzela/`, `statyki/`)
    # is not a decision — it gets its own placement and is coded either way. Asking would
    # be worse than noise: the answer FILTERS the tree and its default ticks only the
    # first group, so a package of `karuzela/` + `posty/` would silently drop one.
    # Ani o folder ŹRÓDŁA, które user wybrał: skoro je wybrał, kodujemy je — a odpowiedź
    # na to pytanie FILTRUJE drzewo i domyślnie zaznacza tylko pierwszą grupę, więc
    # paczka `GDN/` + `Programmatic/` gubiła drugie źródło bez słowa.
    groups = [g for g in (parsed.get("groups") or [])
              if g["name"] not in consumed and not placement_for(src_conf, g["name"])
              and not group_source(g, selected)]
    if groups:
        opts = [{"value": g["name"], "label": f"{g['name']} ({g['source_hint'] or '?'}, "
                 f"{g['n_entries']} plików)"} for g in groups]
        # Nic nie jest zaznaczone domyślnie. Zostały tu WYŁĄCZNIE foldery, które nie
        # należą do żadnego wybranego źródła — a paczka z materiałami afiliacji i
        # programmatic obok GDN nie może po cichu dorzucić obcych wymiarów do zlecenia
        # GDN (dokładnie to zdarzyło się na żywej sesji). Wcześniejszy domyślny
        # `[groups[0]]` zaznaczał pierwszy z nich bez pytania.
        q.append({
            "id": "groups", "type": "multi_choice",
            "prompt": "Zip zawiera foldery/paczki spoza wybranych źródeł. Które jeszcze kodujemy?",
            "options": opts, "default": [],
            "note": "Domyślnie ŻADNEGO — kodujemy tylko wybrane źródła. Zaznacz, jeśli "
                    "materiały z tego folderu też mają wejść (powstanie osobny placement).",
        })
    if line_conflict and line_conflict.get("conflict"):
        ex_name = line_conflict.get("existingLpName") or ""
        m = re.search(r"linia\s*(\d+)", ex_name, re.I)
        reuse_no = int(m.group(1)) if m else None
        q.append({
            "id": "line_conflict", "type": "single_choice",
            "prompt": "W kampanii jest już ta ścieżka i to samo źródło; LP różni się tylko "
                      "parametrem query. Co robimy?",
            "options": [
                {"value": "new", "label": "Dodaj nową linię (inne kreacje)"},
                {"value": "reuse", "label": "Użyj istniejącej / podeślij ponownie te same tagi"},
            ],
            "default": "new",
            "detail": {"existing": line_conflict.get("existingUrl"),
                       "existingLpName": ex_name,
                       "new": line_conflict.get("newUrl"),
                       "reuseLine": ({"number": reuse_no, "lpName": ex_name,
                                      "creativeName": f"linia{reuse_no}"} if reuse_no else None)},
        })
    return q


def compute_tags(proposal):
    """Derive the tag list (site/placement/ad/creative) fresh from the CURRENT
    placements/ads/creatives. Always call this at commit time instead of trusting
    proposal["tags"] verbatim — the UI lets users add placements/ads/creatives after
    the proposal was first built, and a stale tags field would silently miss them."""
    site = proposal["site"]["name"]
    return [{"site": pl.get("site", site), "placement": pl["name"], "ad": a["name"],
             "creative": cr["name"]}
            for pl in proposal["placements"] for a in pl["ads"] for cr in a["creatives"]]


def _line_node(L, fallback_url=None):
    """The proposal's view of one line/landing page."""
    return {"number": L["lineNumber"], "lpName": L["lpName"],
            "creativeName": L.get("creativeName") or f"linia{L['lineNumber']}",
            "path": L.get("path"), "url": L.get("url") or fallback_url,
            "reusedLine": L.get("reused", False), "label": L.get("label"),
            # źródło TEJ strony docelowej, tak jak stoi w nazwie LP — przy zleceniu
            # wieloźródłowym decyduje, na których placementach ta linia się pojawia
            "source": L.get("source"),
            # the keyword the user typed for this page, and whether it had to be
            # dropped (the address is already a landing page under another name)
            "keyword": L.get("keyword"),
            "keywordIgnored": bool(L.get("keywordIgnored"))}


def _unit_folder(u):
    """The top-level zip folder a unit came from, whichever way parse_zip classified
    it. `remarketing/` lands in `group` (it is a GROUP_KEYWORD) while `prospecting/`
    lands in `variant` — for landing-page matching the distinction is meaningless."""
    return u.get("group") or u.get("variant")


BASE_FORMATS = {"gif", "png", "html", "video"}


def file_format(unit, conf, folder=None):
    """The file format a unit's FOLDER declares — `gif` / `png` / `html` / … / None.

    Read from the WORDS of the unit's top-level folder (`SPÓŁKA JPG`, `HTML FRC`,
    `KONTO FIRMOWE GIF`), because a folder distinguishes PNG from JPG while the parsed
    asset type cannot — both arrive as `image`. `aliases` folds interchangeable names
    together (this client's JPG deliveries are PNG placements).

    Deliberately NOT falling back to the asset type: the format may only shape the
    structure when the delivery itself separates formats into folders. Guessing `html`
    from an asset type would suffix every ad of an ordinary single-format package,
    which is noise, not information.

    Also deliberately independent of which landing page a folder belongs to: one folder
    name carries BOTH signals (`SPÓŁKA JPG` = product SPÓŁKA, format JPG), so consuming
    it for the landing page must not lose the format.
    """
    fconf = conf.get("fileFormats") or {}
    aliases = {str(k).lower(): v for k, v in (fconf.get("aliases") or {}).items()}
    known = (set(aliases) | {str(k).lower() for k in (fconf.get("placements") or {})}
             | BASE_FORMATS)
    words = matcher.normalize(folder or _unit_folder(unit) or "").split("_")
    found = next((w for w in words if w in known), None)
    return aliases.get(found, found)


def placement_for(conf, name):
    """The placement a top-level folder maps to for this source, or None when the folder
    names no format this source knows (`placementByFormat`, case-insensitive).

    Needed because `format_hint` from the parser is ZIP-GLOBAL: a Meta package holding
    `karuzela/` and `statyki/` hints "Karuzela" for the whole zip, so the statics ended
    up in a second placement also called Karuzela. The folder a unit actually came from
    is the reliable signal; the config decides both which folders are formats and how
    the placement is spelled (`karuzela` -> `Karuzela`).
    """
    if not name:
        return None
    pbf = {str(k).lower(): v for k, v in (conf.get("placementByFormat") or {}).items()}
    return pbf.get(str(name).lower())


def format_mode(conf):
    """'ignore' (default) / 'adSuffix' / 'placement' — see _fileFormats in source_map."""
    mode = (conf.get("fileFormats") or {}).get("mode") or "ignore"
    return mode if mode in ("ignore", "adSuffix", "placement") else "ignore"


def selected_sources(source, sources=None):
    """Sources of ONE order, primary first, deduplicated — `source` always leads.

    One order may cover several sources when the package separates them by folder
    (`GDN/` + `Programmatic/`). The primary source is what names the order (its Site is
    the badge, its config names the leftover materials); every other selected source
    contributes its OWN Site, placement names and adKey.
    """
    out = [source]
    for s in sources or []:
        if s and s not in out:
            out.append(s)
    return out


def group_source(group, selected):
    """Which SELECTED source a zip folder belongs to, or None.

    Matched on the parser's `source_hint` first (it already maps `fb` -> Facebook) and
    then on the raw folder name, both case-insensitively — a delivery writes `GDN/`,
    `gdn/` or `Programmatic/` as it pleases.
    """
    if not group:
        return None
    hint = (group.get("source_hint") or "").lower()
    name = (group.get("name") or "").lower()
    return next((s for s in selected if s.lower() in (hint, name)), None)


def build_proposal(source, parsed, campaign, line=None, existing=None, source_map=None,
                   campaign_lps=None, target_url=None, line_conflict=None,
                   lines=None, folder_match=None, sources=None, line_addresses=None):
    """
    source       : "GDN"/"Facebook"/... — the PRIMARY source of the order
    sources      : every source of this order (primary first). A package that separates
                   sources by folder (`GDN/` + `Programmatic/`) is trafficked in one go:
                   each folder becomes placements on ITS OWN Site, named by ITS OWN
                   config (placementByFormat/adKey/fileFormats). Materials outside any
                   source folder belong to the primary source.
    parsed       : parse_zip.parse() result
    campaign     : {"id": str|None, "name": str, "status": "existing"|"new"}
    line         : matcher.resolve_line() result — single-line shorthand for `lines`
    lines        : matcher.resolve_lines() result; SEVERAL landing pages in one order,
                   all in this campaign. Each becomes its own creative, carrying its
                   own lpName/lpUrl so the orchestrator creates and registers all of
                   them (see Orchestrator._lp_key).
    folder_match : matcher.match_folders_to_lps() result. A folder mapped to an LP
                   stops being a placement discriminator; materials with no folder
                   mapping go to EVERY line (the "same graphics, both pages" case).
                   Its `map` points at ADDRESSES, not at entries of `lines` — with
                   several sources one address yields one line entry per source.
    line_addresses: for each entry of `lines`, the index of the ADDRESS it came from.
                   Defaults to identity (one entry per address, the single-source case).
    existing     : optional {site: {placement: {ad: [creativeNames]}}}
    campaign_lps : optional [{lpName, lpUrl}] existing landing pages of the campaign
    target_url   : optional full URL being added (shown for the current line)
    """
    source_map = source_map or json.load(open(SRC_MAP, encoding="utf-8"))["sources"]
    selected = selected_sources(source, sources)
    main_conf = source_map.get(source, {"site": source, "placementByFormat": {},
                                        "adKey": "dimension"})
    main_site = main_conf["site"]
    fmt = parsed["format_hint"]
    lines = list(lines or ([line] if line else []))
    line_nodes = [_line_node(L, target_url if i == 0 else None)
                  for i, L in enumerate(lines)]
    multi = len(line_nodes) > 1
    # Z kilkoma źródłami każda linia ma LP na KAŻDE źródło (linia1-GDN obok
    # linia1-Programmatic), więc placement musi brać creative tylko z LP swojego
    # źródła. Filtrujemy WYŁĄCZNIE gdy źródeł jest więcej niż jedno — przy jednym
    # nie ma czego wybierać, a niedopasowanie tokenu opróżniłoby ady po cichu.
    multi_src = len({ln.get("source") for ln in line_nodes if ln.get("source")}) > 1
    fmatch = folder_match or {}
    folder_map = fmatch.get("map") or {}
    # Folders that are landing-page names and NOTHING else, so they must not also split
    # placements (`remarketing/` is not a placement next to `Display`). Deliberately not
    # every key of folder_map: assigning `screening/` to a page says which page its
    # materials serve, it does not stop Screening from being its own format placement.
    consumed = set(fmatch["consumed"]) if "consumed" in fmatch else set(folder_map)
    warnings = list(parsed.get("warnings", []))

    # Which line(s) does each unit feed? A unit from a folder mapped to a landing page
    # feeds only that one; anything unmapped feeds all of them.
    all_idx = list(range(len(line_nodes)))
    # folder -> ADRES, a adres może mieć kilka wpisów (po jednym na źródło)
    addr_of = list(line_addresses or range(len(line_nodes)))
    lines_of_addr = {}
    for j, a in enumerate(addr_of):
        lines_of_addr.setdefault(a, []).append(j)
    units_all = []
    for u in parsed["units"]:
        v = dict(u)
        idx = folder_map.get(_unit_folder(u))
        v["_lines"] = lines_of_addr.get(idx, all_idx) if idx is not None else all_idx
        # remember the folder BEFORE it is cleared below: one folder name carries both
        # signals (`SPÓŁKA JPG` = which page, and which file format), and the format is
        # read later — per source, because each source has its own fileFormats config
        v["_folder"] = _unit_folder(u)
        if v.get("group") in consumed:
            v["group"] = None          # consumed as an LP discriminator, not a placement
        units_all.append(v)

    # one placement per source/format group (GDN -> Display, Screening -> Screening, ...);
    # the None group is the main placement and takes everything outside the remaining
    # groups. Without it those units would be dropped without a trace — reachable as
    # soon as a zip mixes an LP folder (consumed above) with a real format folder.
    groups = [g for g in (parsed.get("groups") or []) if g["name"] not in consumed]
    grouped = {g["name"] for g in groups}
    if not groups or any(u.get("group") not in grouped for u in units_all):
        groups = [None] + groups
    placements, plc_by_key = [], {}
    for g in groups:
        units = ([u for u in units_all if u.get("group") not in grouped] if g is None
                 else [u for u in units_all if u.get("group") == g["name"]])
        if not units:
            continue
        sel_source = group_source(g, selected)          # folder = jedno z WYBRANYCH źródeł
        fmt_folder = placement_for(main_conf, g["name"]) if g else None
        # what the placement reports as its source GROUP: neither a format folder of this
        # source nor a folder of a source the user SELECTED is one — there is nothing to
        # ask about and nothing for the UI to filter by (see build_questions)
        pl_group = None if (g is None or fmt_folder or sel_source) else g["name"]
        if g is None or sel_source:
            # own source (incl. the primary one): own Site, own placement naming, own adKey
            g_source = sel_source or source
            conf = source_map.get(g_source, main_conf)
            g_site = conf.get("site", main_site)
            placement_name = placement_for(conf, fmt) or fmt
        elif fmt_folder:
            # a FORMAT folder of THIS source (Meta: karuzela/, statyki/) — not a foreign
            # source, so the source stays and the config spells the placement
            g_source, g_site, conf, placement_name = source, main_site, main_conf, fmt_folder
        else:                              # obcy folder (np. Screening) na Site źródła
            g_source = g["source_hint"] or g["name"]
            g_site, conf, placement_name = main_site, main_conf, g["name"]
        ad_key = source_map.get(g_source, conf).get("adKey", "dimension")
        # file-format handling is per source too (mode + aliases + placement names)
        mode = format_mode(conf)
        fmt_placements = {str(k).lower(): v for k, v in
                          ((conf.get("fileFormats") or {}).get("placements") or {}).items()}
        for u in units:
            u["_format"] = (file_format(u, conf, u.get("_folder"))
                            if mode != "ignore" else None)

        # `placement` mode: the file format picks the placement (gif -> GIF, html ->
        # HTML, png -> Display), each holding only the dimensions from its own folders.
        # Any other mode leaves the single name derived from the group.
        buckets = {}
        for u in units:
            if mode == "placement" and u.get("_format"):
                key = fmt_placements.get(u["_format"], placement_name)
            elif g is None:
                # The leftover bucket collects everything outside the recognised groups,
                # so its units may come from DIFFERENT format folders (Meta `statyki/`
                # next to `karuzela/`). Each folder the source knows as a format gets its
                # own placement; the zip-global format hint only names what is left.
                # An LP folder is skipped: it says which page the materials serve, not
                # which format they are.
                fld = u.get("_folder")
                key = (placement_for(conf, fld) if fld not in consumed else None
                       ) or placement_name
            else:
                key = placement_name
            buckets.setdefault(key, []).append(u)

        # token źródła tak, jak stoi w nazwie LP (Facebook/Meta -> FB)
        g_token = lp_source(g_source, source_map)
        for plc_name, bucket in buckets.items():
            ex_plc = ((existing or {}).get(g_site, {})).get(plc_name)
            # an ad name can come from several units (prospecting/300x250 and
            # remarketing/300x250 are one 300x250 ad with two creatives), so collect
            # the lines it serves and remember which unit fed each of them
            ads = {}
            for u in bucket:
                base = _ad_name(u, ad_key)
                # numerowany zestaw materiałów (`linia1/` + `linia2/` w paczce) to dwa
                # komplety tych samych wymiarów pod JEDNYM LP — rozróżniane na adzie
                if u.get("set_index"):
                    base = f"{base}_{u['set_index']}"
                # `adSuffix` mode: one placement, the format separates ads (160x600_gif)
                name = (f"{base}_{u['_format']}"
                        if mode == "adSuffix" and u.get("_format") else base)
                slot = ads.setdefault(name, {"unit": u, "by_line": {}})
                for i in u["_lines"]:
                    slot["by_line"].setdefault(i, u)
            ad_nodes = []
            for name, slot in ads.items():
                ex_cre = set((ex_plc or {}).get(name) or [])
                creatives = []
                for i in sorted(slot["by_line"]):
                    u, ln = slot["by_line"][i], line_nodes[i]
                    if (multi_src and ln.get("source")
                            and ln["source"].lower() != (g_token or "").lower()):
                        continue           # LP innego źródła — nie na tym placemencie
                    cr = {"name": ln["creativeName"], "type": u.get("type"),
                          "packaged": u.get("packaged", False),
                          "source_path": u.get("source_path"),
                          "status": _status(ln["creativeName"], ex_cre)}
                    if multi:  # the orchestrator needs an explicit LP per creative
                        cr["lpName"], cr["lpUrl"] = ln["lpName"], ln["url"] or ""
                    creatives.append(cr)
                if not creatives:
                    continue               # nic z tego źródła nie trafia na ten ad
                ad_nodes.append({"name": name,
                                 "dimension": slot["unit"].get("dimension"),
                                 "status": _status(name, ex_plc),
                                 "creatives": creatives})
            ad_nodes.sort(key=lambda a: a["name"])
            # Ten sam Site + ta sama nazwa = JEDEN placement w CM360, więc jeden węzeł
            # w propozycji. Zdarza się, gdy zip ma folder źródła i materiały luzem obok
            # niego (`GDN/` + plik w korzeniu) — wcześniej powstawały dwa identyczne
            # węzły, a orkiestrator dopisywałby ady do tego samego placementu dwa razy.
            key = (g_site, plc_name, pl_group)
            prev = plc_by_key.get(key)
            if prev is None:
                plc_by_key[key] = {
                    "name": plc_name, "group": pl_group, "source": g_source,
                    "site": g_site, "compatibility": "DISPLAY", "size": "1x1",
                    "status": _status(plc_name, (existing or {}).get(g_site)),
                    "ads": ad_nodes,
                }
                placements.append(plc_by_key[key])
                continue
            by_ad = {a["name"]: a for a in prev["ads"]}
            for a in ad_nodes:
                cur = by_ad.get(a["name"])
                if not cur:
                    prev["ads"].append(a)
                    by_ad[a["name"]] = a
                    continue
                have = {(c["name"], c.get("lpName")) for c in cur["creatives"]}
                cur["creatives"] += [c for c in a["creatives"]
                                     if (c["name"], c.get("lpName")) not in have]
            prev["ads"].sort(key=lambda a: a["name"])

    # every Site this order touches, primary first — the orchestrator writes each
    # placement on ITS OWN Site and `/api/commit` refuses when one is missing
    site_names = list(dict.fromkeys([main_site] + [pl["site"] for pl in placements]))
    proposal = {
        "campaign": campaign,
        "source": source,
        "sources": selected,
        "site": {"name": main_site, "status": _status(main_site, existing)},
        "sites": [{"name": n, "status": _status(n, existing),
                   "source": next((pl["source"] for pl in placements if pl["site"] == n),
                                  source)} for n in site_names],
        # `line` stays the primary line for everything that only ever handles one
        # (orchestrator default LP, UI header); `lines` is the full set.
        "line": line_nodes[0],
        "lines": line_nodes,
        "lpFolders": folder_match or None,
        "existingLines": _group_lines(campaign_lps),
        "questions": build_questions(parsed, line_conflict, chosen_source=source,
                                     folder_match=folder_match, lines=line_nodes,
                                     source_map=source_map, sources=selected,
                                     line_addresses=addr_of),
        "placements": placements,
        "warnings": warnings,
    }
    proposal["tags"] = compute_tags(proposal)
    return proposal


def _print_human(p):
    c = p["campaign"]
    print(f"campaign: [{c['status']}] {c['name']} (id={c.get('id')})")
    for ln in p.get("lines") or [p["line"]]:
        print(f"line:     {ln['lpName']}  (creative={ln['creativeName']}, "
              f"reusedLine={ln['reusedLine']})  {ln.get('url') or ''}")
    if p.get("lpFolders", {}) and (p["lpFolders"] or {}).get("map"):
        print(f"folder->LP: {p['lpFolders']['map']}")
    print(f"[SITE {p['site']['status']}] {p['site']['name']}")
    for pl in p["placements"]:
        print(f"   (Placement {pl['status']}) {pl['name']}  {pl['size']}")
        for a in pl["ads"]:
            for cr in a["creatives"]:
                print(f"      Ad [{a['status']:8}] {a['name']:26} "
                      f"-> Creative [{cr['status']:8}] {cr['name']} ({cr['type']})")
    print(f"tags to generate: {len(p['tags'])}")
    if p["warnings"]:
        print(f"! {p['warnings']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 3:
        print(__doc__); sys.exit(1)
    zip_path, source, line_no = args[0], args[1], int(args[2])
    parsed = parse_zip.parse(zip_path)
    campaign = {"id": None, "name": "(demo campaign)", "status": "new"}
    line = {"lineNumber": line_no, "lpName": f"linia{line_no}-{source}",
            "source": source, "path": "(demo/path)", "reused": False}
    p = build_proposal(source, parsed, campaign, line)
    if "--json" in sys.argv:
        print(json.dumps(p, indent=2, ensure_ascii=False))
    else:
        _print_human(p)
