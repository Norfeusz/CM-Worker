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
                  "posty", "link", "remarketing", "rmg", "wp"}
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
    # `wp` tylko jako osobne słowo — jako podciąg trafia w „warszawawpigulce", „WPP" itp.
    if re.search(r"(?<![a-z0-9])wp(?![a-z0-9])", blob):
        return "WP"
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
    Returns [{name, source_hint, n_entries}] when >=1 such folder is present.

    Folder ze słownika jest grupą zawsze. Gdy obok niego stoją INNE foldery, są nimi też
    — nawet jeśli ich nazwy nic nam nie mówią: taka paczka jest evidently rozdzielona po
    źródłach, a nieznany folder to nieznane źródło, nie „resztki". Bez tego paczka
    `GDN/` + `Programmatic/` + `WP/` wpuszczała materiały WP do zlecenia programmatic
    jako resztki (realne zgłoszenie) — czyli dokładnie to, czego nie wolno.
    Foldery zestawów (`linia{N}`, `KV{N}_…`) i foldery wymiarów grupami nie są.
    """
    pairs = _strip_root(names)
    tops = {}
    for _orig, rel in pairs:
        if "/" in rel:
            tops.setdefault(rel.split("/")[0], 0)
            tops[rel.split("/")[0]] += 1
    candidates = [t for t in tops if not _dim(t) and not _set_label(t)]
    known = [t for t in candidates if t.lower() in GROUP_KEYWORDS]
    # Nieznany folder jest grupą tylko wtedy, gdy obok stoi ROZPOZNANY folder źródła —
    # to on jest dowodem, że paczka jest rozdzielona po źródłach (`GDN/` + `WP/`).
    # Bez tego warunku grupami stawały się foldery kart karuzeli (`1`, `2`) i wariantów
    # (`banner-1`), czyli zwykły materiał zlecenia; materiał spoza rozpoznanych folderów
    # łapie osobna siatka bezpieczeństwa (build_proposal.loose_units -> pytanie).
    groups = candidates if (known and len(candidates) > 1) else known
    return [{"name": g, "source_hint": _source_hint([g]), "n_entries": tops[g]}
            for g in sorted(groups)]


def _strip_root(names):
    """Return names with any common leading wrapper folder(s) removed,
    as list of (original, relative) pairs. Recursive: strips 'a/b/...' when
    every entry shares the same single top folder.

    Folder, który NIESIE WYMIAR (`banner_300x250/`), nie jest opakowaniem — obcięcie go
    gubiło wymiar całej dostawy, gdy paczka miała tylko jeden rozmiar (wyłapane testem
    scalania kilku paczek).
    """
    rel = list(names)
    while (rel and all("/" in n for n in rel)
           and len({n.split("/")[0] for n in rel}) == 1
           and not _dim(rel[0].split("/")[0])):
        rel = [n.split("/", 1)[1] for n in rel]
    return list(zip(names, rel))


def _top_folder(rel):
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else None


SET_RE = re.compile(r"^\s*linia\s*(\d+)\s*$", re.I)
# `KV1_NNW_paczki z reformatami` -> KV1: key visual jako zestaw materiałów.
# Świadomie `(?![0-9])`, a NIE `\b`: po „KV1" stoi podkreślenie, które jest znakiem
# słowa, więc granica słowa tam nie występuje i wzorzec z `\b` nie trafiał nigdy.
KV_RE = re.compile(r"^\s*(KV\s*\d+)(?![0-9])", re.I)


def _set_label(seg):
    """Etykieta zestawu z nazwy JEDNEGO segmentu ścieżki, albo None."""
    m = SET_RE.match(seg)
    if m:
        return m.group(1)                      # `linia2/` -> sufiks ada `_2`
    m = KV_RE.match(seg)
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()    # `KV1_…/` -> `_KV1`
    return None


def _set_index(rel):
    """Etykieta ZESTAWU materiałów z folderu na dowolnym poziomie ścieżki.

    Paczki przychodzą jako `GDN/linia1/banner_300x250/…` + `GDN/linia2/banner_300x250/…`,
    czyli dwa komplety tych samych wymiarów. To NIE są dwie strony docelowe (ustalone
    z użytkownikiem): kodujemy je pod jednym LP i rozróżniamy na poziomie ada
    (`300x250_1`, `300x250_2`). Bez tego oba komplety zwijały się w jeden ad i połowa
    materiałów przepadała bez śladu.

    Ten sam wzorzec nosi key visual: `KV1_NNW_paczki z reformatami/` +
    `KV3_NNW_…/` to dwa komplety tych samych wymiarów pod jednym LP, rozróżniane
    na adzie (`240x400_KV1`, `240x400_KV3`) — dokładnie tak, jak poprosił użytkownik.
    """
    for seg in rel.split("/")[:-1]:
        lab = _set_label(seg)
        if lab:
            return lab
    return None


def _variant(rel):
    """Meaningful folder-based variant: the top folder after root-strip, unless it is
    itself just a dimension (then it's a size folder) or a set folder (`linia{N}`,
    `KV{N}_…` — an ad-level distinction, handled by _set_index)."""
    top = _top_folder(rel)
    if top and not _dim(top) and not _set_label(top):
        return top
    return None


def _card_index(base):
    m = re.search(r"(?:^|[-_])(\d{1,2})\.(?:png|jpg|jpeg|mp4|gif)$", base, re.I)
    return m.group(1) if m else None


def _package_dims(inner_names):
    """{wymiar: [pliki]} dla zawartości JEDNEJ zagnieżdżonej paczki.

    Wymiar czytany z folderu (`240x400/index.html`), a gdy paczka jest płaska — z nazwy
    pliku. Jednostki bez rozpoznanego wymiaru są pomijane (preview, manifesty), bo nie
    są materiałem do trafficowania. Kilka wymiarów = paczka wielu banerów, jeden = jeden
    baner (i wtedy zostaje przy dotychczasowej obsłudze „zip = jedna jednostka").
    """
    out = {}
    for n in _strip_root_names(inner_names):
        dim = _dim(_top_folder(n) or "") or _dim(os.path.basename(n)) or _dim(n)
        if dim:
            out.setdefault(dim, []).append(n)
    return out


def _strip_root_names(names):
    return [rel for _o, rel in _strip_root(names)]


HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def _mailing_htmls(names):
    """Pliki HTML, które mogą BYĆ mailingiem (a nie podglądem banera).

    `index.html` traktujemy jako mailing zawsze — tak przychodzą wysyłki, także po
    kilka w jednej paczce (`mail1/index.html`, `mail2/index.html`). Gdy indeksu nie ma,
    bierzemy pojedyncze pliki HTML, ale tylko jeśli jest ich mało: paczka banerów HTML5
    ma po jednym HTML na wymiar i skanowanie ich wszystkich to szukanie mailingu tam,
    gdzie go nie ma.
    """
    htmls = [n for n in names if n.lower().endswith((".html", ".htm"))]
    idx = [n for n in htmls if os.path.basename(n).lower().startswith("index.")]
    return sorted(idx) if idx else (sorted(htmls) if len(htmls) <= 3 else [])


def _mailings(zf, names):
    """Mailingi w paczce: po jednym na plik HTML, z UNIKALNYMI linkami w kolejności
    występowania w dokumencie.

    Kodujemy tylko `http(s)` — domena może być dowolna. Linki bez adresu (`#`, `mailto:`,
    `tel:`) są LICZONE i raportowane, nie milcząco gubione: w realnej wysyłce główny CTA
    bywa placeholderem `#`, a i tak musi dostać swoją stronę docelową — użytkownik dopisze
    ją w edytorze.
    """
    out = []
    for n in _mailing_htmls(names):
        try:
            html = zf.read(n).decode("utf-8", "replace")
        except Exception:
            continue
        seen, links, skipped = set(), [], []
        for h in HREF_RE.findall(html):
            h = h.strip()
            if not h.lower().startswith(("http://", "https://")):
                if h not in skipped:
                    skipped.append(h)
                continue
            if h in seen:
                continue
            seen.add(h)
            links.append(h)
        if links or skipped:
            out.append({"file": n, "links": links, "skippedLinks": skipped})
    return out


def _parse_units(zf):
    """Return list of ad-unit proposals. Handles nested per-size zips too."""
    pairs = _strip_root(_list_names(zf))
    units = []

    # Case A: nested zips. Two different things arrive this way and mixing them up
    # silently loses materials:
    #   * ONE banner per zip (the size is in the zip's own name) -> one unit, as before;
    #   * a whole PACKAGE per zip (`..._kv1_gdn.zip` holding 240x400/, 250x360/, …) ->
    #     one unit per dimension INSIDE it. Wcześniej taka paczka dawała jedną jednostkę
    #     z PIERWSZYM napotkanym wymiarem, więc z 8 wymiarów GDN zostawał jeden — a
    #     w drzewie lądował wymiar z paczki innego źródła. Zgłoszone z żywej sesji.
    for orig, rel in pairs:
        if not rel.lower().endswith(".zip"):
            continue
        own_dim = _dim(os.path.basename(rel)) or _dim(rel)
        atype, inner = "html5", []
        try:
            with zf.open(orig) as fh:
                inner = _list_names(zipfile.ZipFile(io.BytesIO(fh.read())))
                atype = _asset_type(inner)
        except Exception:
            pass
        # źródło TEJ paczki bierze się z jej własnej nazwy (`..._kv1_gdn.zip`), nie z
        # całego zipa — inaczej materiały afiliacji trafiłyby do zlecenia GDN
        pkg_src = None if own_dim else _source_hint([os.path.basename(rel)])
        inner_dims = _package_dims(inner) if not own_dim else {}
        if len(inner_dims) > 1:
            for dim, names in sorted(inner_dims.items()):
                units.append({"dimension": dim, "variant": _variant(rel),
                              "card_index": None, "set_index": _set_index(rel),
                              "type": _asset_type(names), "packaged": True,
                              "package": os.path.basename(rel), "package_source": pkg_src,
                              "source_path": f"{orig}/{dim}", "n_files": len(names)})
            continue
        # JEDEN baner w zipie — celowo BEZ pól `package`: nazwa takiego zipa niesie
        # wymiar (`160x600_gdn 1.zip`, `500x400.zip`), a nie źródło, więc wyciąganie
        # z niej grupy robiło grupy o nazwach wymiarów i dublowało istniejące (`gdn 1`
        # obok folderu `GDN`). Grupę takich jednostek nadaje folder, jak dotąd.
        units.append({"dimension": own_dim or _dim(" ".join(inner)),
                      "variant": _variant(rel), "card_index": None,
                      "set_index": _set_index(rel), "type": atype, "packaged": True,
                      "source_path": orig, "n_files": 1})

    # Case B: loose files, grouped by (dimension, variant, card)
    groups = {}
    for orig, rel in pairs:
        if rel.lower().endswith(".zip"):
            continue
        base = os.path.basename(rel)
        dim = _dim(base) or _dim(_top_folder(rel) or "") or _dim(rel)
        key = (dim, _variant(rel), _card_index(base), _set_index(rel))
        groups.setdefault(key, {"orig": orig, "names": []})["names"].append(rel)

    for (dim, variant, card, sset), g in groups.items():
        units.append({"dimension": dim, "variant": variant, "card_index": card,
                      "set_index": sset, "type": _asset_type(g["names"]),
                      "packaged": False,
                      "source_path": os.path.dirname(g["orig"]) or "/",
                      "n_files": len(g["names"])})

    # Dedup: if a packaged .zip exists for (dim,variant,set), drop the unpacked folder dup
    packaged = {(u["dimension"], u["variant"], u.get("set_index"))
                for u in units if u["packaged"]}
    units = [u for u in units if u["packaged"]
             or (u["dimension"], u["variant"], u.get("set_index")) not in packaged]

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
        mailings = _mailings(zf, names)
    hint_blob = names + [fname]
    atype = _asset_type(names)
    if atype == "other" and units:  # outer had only nested zips
        atype = units[0]["type"]
    units.sort(key=lambda u: (str(u.get("variant") or ""),
                              str(u.get("dimension") or ""),
                              str(u.get("set_index") or ""),
                              str(u.get("card_index") or "")))
    dims = sorted({u["dimension"] for u in units if u["dimension"]})
    variants = sorted({u["variant"] for u in units if u["variant"]})
    groups = _detect_groups(names)
    for u in units:
        sp = "/" + u["source_path"].replace("\\", "/") + "/"
        u["group"] = next((g["name"] for g in groups if f"/{g['name']}/" in sp), None)

    # Grupa z nazwy ZAGNIEŻDŻONEJ PACZKI (`…_kv1_gdn.zip` / `…_kv1_afiliacja.zip`).
    # Bez tego cały podział na źródła był niewidoczny i materiały afiliacji oraz
    # programmatic wpadały do zlecenia GDN jako „reszta" — a tak właśnie do drzewa
    # trafiły wymiary z obcych paczek (realne zgłoszenie). Etykietą jest rozpoznane
    # źródło, a gdy nie jest znane — ostatni człon nazwy paczki (`afiliacja`), żeby
    # KV1 i KV3 tej samej paczki należały do JEDNEJ grupy, a nie dwóch.
    pkg_counts = {}
    for u in units:
        if not u.get("package"):
            continue
        stem = os.path.splitext(u["package"])[0]
        gname = u.get("package_source") or stem.split("_")[-1]
        u["group"] = gname
        pkg_counts[gname] = pkg_counts.get(gname, 0) + 1
    have = {g["name"] for g in groups}
    groups += [{"name": n, "source_hint": _source_hint([n]), "n_entries": c}
               for n, c in sorted(pkg_counts.items()) if n not in have]

    warnings = []
    if any(u["dimension"] is None for u in units):
        warnings.append("some units have no detectable dimension")
    if dropped:
        warnings.append(f"ignored {len(dropped)} dimensionless file(s): {dropped[:3]}")
    if len(groups) > 1:
        warnings.append("multiple source/format folders detected "
                        f"({[g['name'] for g in groups]}) — ASK which to code")
    if mailings and any(m["skippedLinks"] for m in mailings):
        skipped = sorted({h for m in mailings for h in m["skippedLinks"]})
        warnings.append(f"mailing: {len(skipped)} link(i) bez adresu do trafficowania "
                        f"({skipped[:3]}) — jeśli któryś to CTA, dopisz jego adres")
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
        # mailingi: po jednym na plik HTML, z linkami do zakodowania jako strony docelowe
        "mailings": mailings,
        "warnings": warnings,
    }


def merge_parsed(parts):
    """Kilka PACZEK w jednym zleceniu -> jedna struktura wynikowa.

    `parts`: [{"parsed": …, "source": "GDN"|None, "path": <ścieżka zipa>, "name": …}].
    Dostawy przychodzą albo jako jedna paczka z folderami per źródło, albo — i to coraz
    częściej — jako osobny zip na źródło. Scalamy je tutaj, żeby wszystko dalej (grupy,
    placementy per źródło, pytania o obce foldery) działało jednym mechanizmem.

    Zasady:
      * jednostka, która MA już grupę z wnętrza swojej paczki (`GDN/` w środku), tę grupę
        zachowuje — podział wewnątrz zipa jest dokładniejszy niż przypisanie całego zipa;
      * jednostka bez grupy dostaje ŹRÓDŁO swojej paczki, więc materiały z `..._gdn.zip`
        nie mogą wejść do zlecenia programmatica jako „resztki";
      * `_zip` na jednostce mówi, z którego pliku ją wziąć przy uploadzie — dwie paczki
        mogą mieć w środku identyczne ścieżki (`300x250/index.html`).
    """
    parts = [p for p in parts if p.get("parsed")]
    if not parts:
        return None
    if len(parts) == 1 and not parts[0].get("source"):
        return parts[0]["parsed"]

    units, groups, warnings, mailings = [], [], [], []
    dims, variants, have = set(), set(), set()
    for p in parts:
        pr, src = p["parsed"], p.get("source")
        name = p.get("name") or os.path.basename(p.get("path") or "") or "paczka"
        inner = {g["name"] for g in pr.get("groups") or []}
        used_src = False
        for u in pr.get("units") or []:
            v = dict(u, _zip=p.get("path"), _zipName=name)
            if v.get("group") is None and src:
                v["group"] = src
                used_src = True
            units.append(v)
        for g in pr.get("groups") or []:
            if g["name"] not in have:
                groups.append(g)
                have.add(g["name"])
        if used_src and src not in have:
            groups.append({"name": src, "source_hint": src,
                           "n_entries": sum(1 for u in units if u.get("group") == src)})
            have.add(src)
        dims |= {d for d in pr.get("dimensions") or []}
        variants |= {v for v in pr.get("variants") or [] if v not in inner}
        mailings += [dict(m, zip=p.get("path"), zipName=name)
                     for m in pr.get("mailings") or []]
        warnings += [f"{name}: {w}" for w in pr.get("warnings") or []]

    first = parts[0]["parsed"]
    return {
        "file": " + ".join(p.get("name") or "?" for p in parts),
        "source_hint": first.get("source_hint"),
        "format_hint": first.get("format_hint"),
        "groups": groups,
        "asset_type": first.get("asset_type"),
        "dimensions": sorted(dims),
        "variants": sorted(variants),
        "n_units": len(units),
        "units": units,
        "mailings": mailings,
        "warnings": warnings,
        # z ilu paczek to zlecenie — UI pokazuje to wprost, a plan zapisu ma czym
        # rozstrzygnąć, z którego pliku brać materiał
        "packages": [{"name": p.get("name"), "source": p.get("source"),
                      "units": sum(1 for u in units
                                   if u.get("_zipName") == (p.get("name") or ""))}
                     for p in parts],
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
