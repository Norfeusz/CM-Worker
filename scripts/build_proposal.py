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
                    lines=None):
    """Decision points to surface in the UI before/while building the tree."""
    q = []
    lns = lines or []
    # a folder already used to tell landing pages apart is settled — do not also ask
    # about it as a source/format group
    consumed = set((folder_match or {}).get("consumed") or [])
    for prob in matcher.unresolved_lp_folders(folder_match, len(lns)):
        f = prob["folder"]
        opts = [{"value": str(i), "label": f"{l['creativeName']} → {l['lpName']}"}
                for i, l in enumerate(lns)]
        opts.append({"value": "all", "label": "wszystkie LP (te same materiały pod każdą stronę)"})
        q.append({
            "id": f"lp_folder:{f}", "type": "single_choice", "rebuild": True,
            "prompt": f"Do której strony docelowej należą materiały z folderu „{f}”?",
            "options": opts, "default": "all",
            "note": ("Nazwa folderu pasuje do kilku LP — nie zgadujemy."
                     if prob["candidates"] else
                     "Pozostałe foldery udało się przypisać do LP, tego nie — sprawdź."),
        })
    groups = [g for g in (parsed.get("groups") or []) if g["name"] not in consumed]
    if groups:
        opts = [{"value": g["name"], "label": f"{g['name']} ({g['source_hint'] or '?'}, "
                 f"{g['n_entries']} plików)"} for g in groups]
        default = [g["name"] for g in groups
                   if chosen_source and (g["source_hint"] == chosen_source or g["name"].lower() == chosen_source.lower())]
        q.append({
            "id": "groups", "type": "multi_choice",
            "prompt": "Zip zawiera kilka folderów źródeł/formatów. Które kodujemy?",
            "options": opts, "default": default or [groups[0]["name"]],
            "note": "Folder inny niż główne źródło (np. Screening) → osobny placement obok Display.",
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
            "reusedLine": L.get("reused", False), "label": L.get("label")}


def _unit_folder(u):
    """The top-level zip folder a unit came from, whichever way parse_zip classified
    it. `remarketing/` lands in `group` (it is a GROUP_KEYWORD) while `prospecting/`
    lands in `variant` — for landing-page matching the distinction is meaningless."""
    return u.get("group") or u.get("variant")


BASE_FORMATS = {"gif", "png", "html", "video"}


def file_format(unit, conf):
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
    words = matcher.normalize(_unit_folder(unit) or "").split("_")
    found = next((w for w in words if w in known), None)
    return aliases.get(found, found)


def format_mode(conf):
    """'ignore' (default) / 'adSuffix' / 'placement' — see _fileFormats in source_map."""
    mode = (conf.get("fileFormats") or {}).get("mode") or "ignore"
    return mode if mode in ("ignore", "adSuffix", "placement") else "ignore"


def build_proposal(source, parsed, campaign, line=None, existing=None, source_map=None,
                   campaign_lps=None, target_url=None, line_conflict=None,
                   lines=None, folder_match=None):
    """
    source       : "GDN"/"Facebook"/...
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
    existing     : optional {site: {placement: {ad: [creativeNames]}}}
    campaign_lps : optional [{lpName, lpUrl}] existing landing pages of the campaign
    target_url   : optional full URL being added (shown for the current line)
    """
    source_map = source_map or json.load(open(SRC_MAP, encoding="utf-8"))["sources"]
    main_conf = source_map.get(source, {"site": source, "placementByFormat": {},
                                        "adKey": "dimension"})
    main_site = main_conf["site"]
    fmt = parsed["format_hint"]
    lines = list(lines or ([line] if line else []))
    line_nodes = [_line_node(L, target_url if i == 0 else None)
                  for i, L in enumerate(lines)]
    multi = len(line_nodes) > 1
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
    mode = format_mode(main_conf)
    fmt_placements = {str(k).lower(): v for k, v in
                      ((main_conf.get("fileFormats") or {}).get("placements") or {}).items()}
    units_all = []
    for u in parsed["units"]:
        v = dict(u)
        idx = folder_map.get(_unit_folder(u))
        v["_lines"] = [idx] if idx is not None else all_idx
        # read the format BEFORE the folder is cleared below: one folder name carries
        # both signals (`SPÓŁKA JPG` = which page, and which file format)
        v["_format"] = file_format(u, main_conf) if mode != "ignore" else None
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
    placements = []
    for g in groups:
        units = ([u for u in units_all if u.get("group") not in grouped] if g is None
                 else [u for u in units_all if u.get("group") == g["name"]])
        if not units:
            continue
        if g is None or (g["source_hint"] or g["name"]).lower() == source.lower():
            g_source, g_site = source, main_site
            placement_name = main_conf.get("placementByFormat", {}).get(fmt, fmt)
        else:                              # extra format folder (e.g. Screening) on same site
            g_source = g["source_hint"] or g["name"]
            g_site = main_site
            placement_name = g["name"]
        ad_key = source_map.get(g_source, main_conf).get("adKey", "dimension")

        # `placement` mode: the file format picks the placement (gif -> GIF, html ->
        # HTML, png -> Display), each holding only the dimensions from its own folders.
        # Any other mode leaves the single name derived from the group.
        buckets = {}
        for u in units:
            key = (fmt_placements.get(u["_format"], placement_name)
                   if mode == "placement" and u.get("_format") else placement_name)
            buckets.setdefault(key, []).append(u)

        for plc_name, bucket in buckets.items():
            ex_plc = ((existing or {}).get(g_site, {})).get(plc_name)
            # an ad name can come from several units (prospecting/300x250 and
            # remarketing/300x250 are one 300x250 ad with two creatives), so collect
            # the lines it serves and remember which unit fed each of them
            ads = {}
            for u in bucket:
                base = _ad_name(u, ad_key)
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
                    cr = {"name": ln["creativeName"], "type": u.get("type"),
                          "packaged": u.get("packaged", False),
                          "source_path": u.get("source_path"),
                          "status": _status(ln["creativeName"], ex_cre)}
                    if multi:  # the orchestrator needs an explicit LP per creative
                        cr["lpName"], cr["lpUrl"] = ln["lpName"], ln["url"] or ""
                    creatives.append(cr)
                ad_nodes.append({"name": name,
                                 "dimension": slot["unit"].get("dimension"),
                                 "status": _status(name, ex_plc),
                                 "creatives": creatives})
            ad_nodes.sort(key=lambda a: a["name"])
            placements.append({
                "name": plc_name, "group": (g and g["name"]), "source": g_source,
                "site": g_site, "compatibility": "DISPLAY", "size": "1x1",
                "status": _status(plc_name, (existing or {}).get(g_site)),
                "ads": ad_nodes,
            })

    proposal = {
        "campaign": campaign,
        "source": source,
        "site": {"name": main_site, "status": _status(main_site, existing)},
        # `line` stays the primary line for everything that only ever handles one
        # (orchestrator default LP, UI header); `lines` is the full set.
        "line": line_nodes[0],
        "lines": line_nodes,
        "lpFolders": folder_match or None,
        "existingLines": _group_lines(campaign_lps),
        "questions": build_questions(parsed, line_conflict, chosen_source=source,
                                     folder_match=folder_match, lines=line_nodes),
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
