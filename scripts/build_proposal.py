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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip


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


def build_questions(parsed, line_conflict=None, chosen_source=None):
    """Decision points to surface in the UI before/while building the tree."""
    q = []
    groups = parsed.get("groups") or []
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


def build_proposal(source, parsed, campaign, line, existing=None, source_map=None,
                   campaign_lps=None, target_url=None, line_conflict=None):
    """
    source       : "GDN"/"Facebook"/...
    parsed       : parse_zip.parse() result
    campaign     : {"id": str|None, "name": str, "status": "existing"|"new"}
    line         : matcher.resolve_line() result (has lineNumber, lpName, source, path)
    existing     : optional {site: {placement: {ad: [creativeNames]}}}
    campaign_lps : optional [{lpName, lpUrl}] existing landing pages of the campaign
    target_url   : optional full URL being added (shown for the current line)
    """
    source_map = source_map or json.load(open(SRC_MAP, encoding="utf-8"))["sources"]
    main_conf = source_map.get(source, {"site": source, "placementByFormat": {},
                                        "adKey": "dimension"})
    main_site = main_conf["site"]
    fmt = parsed["format_hint"]
    creative_name = f"linia{line['lineNumber']}"
    warnings = list(parsed.get("warnings", []))

    # one placement per source/format group (GDN -> Display, Screening -> Screening, ...);
    # no groups -> a single placement from all units.
    groups = parsed.get("groups") or [None]
    placements = []
    for g in groups:
        units = parsed["units"] if g is None else [u for u in parsed["units"]
                                                   if u.get("group") == g["name"]]
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

        ex_plc = ((existing or {}).get(g_site, {})).get(placement_name)
        ads = {}
        for u in units:
            ads.setdefault(_ad_name(u, ad_key), u)
        ad_nodes = [{
            "name": name, "dimension": u.get("dimension"),
            "status": _status(name, ex_plc),
            "creatives": [{"name": creative_name, "type": u.get("type"),
                          "packaged": u.get("packaged", False),
                          "source_path": u.get("source_path"),
                          "status": _status(creative_name, set((ex_plc or {}).get(name) or []))}],
        } for name, u in ads.items()]
        ad_nodes.sort(key=lambda a: a["name"])
        placements.append({
            "name": placement_name, "group": (g and g["name"]), "source": g_source,
            "site": g_site, "compatibility": "DISPLAY", "size": "1x1",
            "status": _status(placement_name, (existing or {}).get(g_site)),
            "ads": ad_nodes,
        })

    proposal = {
        "campaign": campaign,
        "source": source,
        "site": {"name": main_site, "status": _status(main_site, existing)},
        "line": {
            "number": line["lineNumber"], "lpName": line["lpName"],
            "creativeName": creative_name, "path": line.get("path"),
            "url": target_url, "reusedLine": line.get("reused", False),
        },
        "existingLines": _group_lines(campaign_lps),
        "questions": build_questions(parsed, line_conflict, chosen_source=source),
        "placements": placements,
        "warnings": warnings,
    }
    proposal["tags"] = compute_tags(proposal)
    return proposal


def _print_human(p):
    c = p["campaign"]
    print(f"campaign: [{c['status']}] {c['name']} (id={c.get('id')})")
    print(f"line:     {p['line']['lpName']}  (creative={p['line']['creativeName']}, "
          f"reusedLine={p['line']['reusedLine']})")
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
