"""Zip structure parser for the CM360 tool.

Extracts what a creatives zip RELIABLY contains: format, dimensions, variant
groups, and asset type. Lines/audiences are NOT inferred here (they come from
the request message + user). Output is a proposal to be verified/edited in the UI.

Usage:
  py parser/parse_zip.py <file.zip> [<file2.zip> ...]
  py parser/parse_zip.py --json <file.zip>
"""
import io
import json
import os
import re
import sys
import zipfile

DIM_RE = re.compile(r"(\d{2,4})\s*[xX×]\s*(\d{2,4})")
# top-level folder names that denote a SOURCE/FORMAT group (ask user), not a variant
GROUP_KEYWORDS = {"gdn", "screening", "facebook", "meta", "fb", "demgen", "demandgen",
                  "programmatic", "mailing", "video", "display", "karuzela", "animacje",
                  "posty", "link", "remarketing", "rmg"}
JUNK_RE = re.compile(r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|\.git)(/|$)|(^|/)\._")
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
GIF_EXT = {".gif"}
HTML_EXT = {".html", ".htm"}


def _is_junk(name):
    return bool(JUNK_RE.search(name))


def _dim(text):
    m = DIM_RE.search(text)
    return f"{m.group(1)}x{m.group(2)}" if m else None


def _list_names(zf):
    return [n for n in zf.namelist() if not n.endswith("/") and not _is_junk(n)]


def _asset_type(names):
    exts = {os.path.splitext(n)[1].lower() for n in names}
    if exts & HTML_EXT:
        return "html5"
    if exts & VIDEO_EXT:
        return "video"
    if exts & GIF_EXT:
        return "gif"
    if exts & IMG_EXT:
        return "image"
    return "other"


def _source_hint(all_names):
    blob = " ".join(all_names).lower()
    if "demgen" in blob or "demand" in blob:
        return "DemGen"
    if "gdn" in blob:
        return "GDN"
    if "meta" in blob or "facebook" in blob or "fb" in blob:
        return "Facebook"
    if "mail" in blob:
        return "Mailing"
    if "programmatic" in blob:
        return "Programmatic"
    return None


def _format_hint(all_names, atype):
    blob = " ".join(all_names).lower()
    if "karuzela" in blob or "carousel" in blob:
        return "Karuzela"
    if atype == "video":
        return "Video"
    if "mail" in blob:
        return "Mailing"
    return "Display"


def _detect_groups(names):
    """Detect top-level SOURCE/FORMAT folders (e.g. GDN, Screening) after root-strip.
    Returns [{name, source_hint, n_entries}] when >=1 such folder is present."""
    pairs = _strip_root(names)
    tops = {}
    for _orig, rel in pairs:
        if "/" in rel:
            tops.setdefault(rel.split("/")[0], 0)
            tops[rel.split("/")[0]] += 1
    groups = [t for t in tops if t.lower() in GROUP_KEYWORDS]
    return [{"name": g, "source_hint": _source_hint([g]), "n_entries": tops[g]}
            for g in sorted(groups)]


def _strip_root(names):
    """Return names with any common leading wrapper folder(s) removed,
    as list of (original, relative) pairs. Recursive: strips 'a/b/...' when
    every entry shares the same single top folder."""
    rel = list(names)
    while rel and all("/" in n for n in rel) and len({n.split("/")[0] for n in rel}) == 1:
        rel = [n.split("/", 1)[1] for n in rel]
    return list(zip(names, rel))


def _top_folder(rel):
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else None


def _variant(rel):
    """Meaningful folder-based variant: the top folder after root-strip,
    unless it is itself just a dimension (then it's a size folder, not a variant)."""
    top = _top_folder(rel)
    if top and not _dim(top):
        return top
    return None


def _card_index(base):
    m = re.search(r"(?:^|[-_])(\d{1,2})\.(?:png|jpg|jpeg|mp4|gif)$", base, re.I)
    return m.group(1) if m else None


def _parse_units(zf):
    """Return list of ad-unit proposals. Handles nested per-size zips too."""
    pairs = _strip_root(_list_names(zf))
    units = []

    # Case A: nested per-size zips (each inner zip = one uploadable HTML5 unit)
    for orig, rel in pairs:
        if not rel.lower().endswith(".zip"):
            continue
        dim = _dim(os.path.basename(rel)) or _dim(rel)
        atype = "html5"
        try:
            with zf.open(orig) as fh:
                inner = _list_names(zipfile.ZipFile(io.BytesIO(fh.read())))
                atype = _asset_type(inner)
                dim = dim or _dim(" ".join(inner))
        except Exception:
            pass
        units.append({"dimension": dim, "variant": _variant(rel), "card_index": None,
                      "type": atype, "packaged": True, "source_path": orig, "n_files": 1})

    # Case B: loose files, grouped by (dimension, variant, card)
    groups = {}
    for orig, rel in pairs:
        if rel.lower().endswith(".zip"):
            continue
        base = os.path.basename(rel)
        dim = _dim(base) or _dim(_top_folder(rel) or "") or _dim(rel)
        key = (dim, _variant(rel), _card_index(base))
        groups.setdefault(key, {"orig": orig, "names": []})["names"].append(rel)

    for (dim, variant, card), g in groups.items():
        units.append({"dimension": dim, "variant": variant, "card_index": card,
                      "type": _asset_type(g["names"]), "packaged": False,
                      "source_path": os.path.dirname(g["orig"]) or "/",
                      "n_files": len(g["names"])})

    # Dedup: if a packaged .zip exists for (dim,variant), drop the unpacked folder dup
    packaged = {(u["dimension"], u["variant"]) for u in units if u["packaged"]}
    units = [u for u in units
             if u["packaged"] or (u["dimension"], u["variant"]) not in packaged]

    # Drop noise: dimensionless html/other wrappers (preview.html, folder roots)
    dropped = [u["source_path"] for u in units
               if u["dimension"] is None and u["type"] in ("html5", "other")]
    units = [u for u in units
             if not (u["dimension"] is None and u["type"] in ("html5", "other"))]
    return units, dropped


def parse(path):
    fname = os.path.basename(path)
    with zipfile.ZipFile(path) as zf:
        names = _list_names(zf)
        units, dropped = _parse_units(zf)
    hint_blob = names + [fname]
    atype = _asset_type(names)
    if atype == "other" and units:  # outer had only nested zips
        atype = units[0]["type"]
    units.sort(key=lambda u: (str(u.get("variant") or ""),
                              str(u.get("dimension") or ""),
                              str(u.get("card_index") or "")))
    dims = sorted({u["dimension"] for u in units if u["dimension"]})
    variants = sorted({u["variant"] for u in units if u["variant"]})
    groups = _detect_groups(names)
    for u in units:
        sp = "/" + u["source_path"].replace("\\", "/") + "/"
        u["group"] = next((g["name"] for g in groups if f"/{g['name']}/" in sp), None)

    warnings = []
    if any(u["dimension"] is None for u in units):
        warnings.append("some units have no detectable dimension")
    if dropped:
        warnings.append(f"ignored {len(dropped)} dimensionless file(s): {dropped[:3]}")
    if len(groups) > 1:
        warnings.append("multiple source/format folders detected "
                        f"({[g['name'] for g in groups]}) — ASK which to code")
    return {
        "file": os.path.basename(path),
        "source_hint": _source_hint(hint_blob),
        "format_hint": _format_hint(hint_blob, atype),
        "groups": groups,
        "asset_type": atype,
        "dimensions": dims,
        "variants": variants,
        "n_units": len(units),
        "units": units,
        "warnings": warnings,
    }


def _print_human(r):
    print(f"\n=== {r['file']} ===")
    print(f"  source~{r['source_hint']}  format~{r['format_hint']}  "
          f"type={r['asset_type']}  units={r['n_units']}")
    print(f"  dimensions: {r['dimensions']}")
    if r.get("groups"):
        print(f"  GROUPS (source/format folders — ask which to code): "
              f"{[(g['name'], g['source_hint'], g['n_entries']) for g in r['groups']]}")
    if r["variants"]:
        print(f"  variants:   {r['variants']}")
    if r["warnings"]:
        print(f"  ! {r['warnings']}")
    for u in r["units"]:
        tag = " ".join(x for x in [
            f"dim={u['dimension']}",
            f"var={u['variant']}" if u["variant"] else "",
            f"card={u['card_index']}" if u["card_index"] else "",
            f"[{u['type']}]",
        ] if x)
        print(f"     - {tag:42} @ {u['source_path']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    results = [parse(p) for p in args]
    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for r in results:
            _print_human(r)
