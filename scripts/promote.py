"""Promocja zatwierdzonych decyzji AI do configu — żeby ten sam przypadek nie wymagał
modelu drugi raz.

Bez tego AI jest kosztem STAŁYM: każde zlecenie z folderem `Screening/` znów pyta model,
co to jest, mimo że człowiek raz już odpowiedział. Promocja zamienia jednorazową
odpowiedź w deterministyczną regułę — i od tego momentu ścieżka AI się dla niej nie
uruchamia.

Co da się promować (i tylko to):
  * `group_mappings` -> `source_map.json` — folder/źródło ma trwałe Site, placement i adKey;
  * `advertiser_guess` -> `advertiser_map.json` — anchor ścieżki ma trwały advertiserId.
`ad_naming`, `lines` i `resolved_questions` są z natury JEDNORAZOWE: dotyczą tego zlecenia,
nie reguły, a wpisane do configu zaśmieciłyby go na zawsze (prompt roli (a) zakazuje zresztą
zwracania `group_mappings` z pustym folderem właśnie dlatego).

Podział jak wszędzie w tym projekcie: model PROPONUJE, człowiek ZATWIERDZA, kod STOSUJE.
`changes()` i `apply_changes()` są czyste — bez plików, bez sieci — więc dają się testować
na wprost; `save()` jest jedyną funkcją dotykającą dysku i robi kopię zapasową.
"""
import datetime
import json
import os
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_MAP = os.path.join(BASE, "config", "source_map.json")
ADV_MAP = os.path.join(BASE, "config", "advertiser_map.json")

# Poniżej tego progu nie proponujemy NICZEGO do zapisu w configu. Reguła w configu działa
# już bez nadzoru i na wszystkich kolejnych zleceniach, więc kosztuje więcej niż zła
# odpowiedź w jednym drzewie — próg jest tu wyżej niż przy zwykłym stosowaniu sugestii.
MIN_CONFIDENCE = 0.8

ADKEYS = {"dimension", "variant", "variant_dim_card"}


def _prov(reason, confidence):
    """Proweniencja wpisu — bez niej nie da się później odróżnić reguły napisanej przez
    człowieka od tej, którą dopisał model, ani jej sensownie cofnąć."""
    return {"_source": "ai", "_addedAt": datetime.date.today().isoformat(),
            "_reason": (reason or "")[:300], "_confidence": round(float(confidence or 0), 2)}


def changes(suggestions, source_map, advertiser_map, advertiser_id=None):
    """Co z tych sugestii DA SIĘ zapisać w configu jako trwała reguła.

    Zwraca listę `{kind, key, path, current, proposed, reason, confidence, blocked}`.
    Wpis z `blocked` jest pokazywany, ale NIE nadaje się do zatwierdzenia — niesie powód,
    żeby user wiedział, czego brakuje (np. nieznany `adKey`, brak Site, za niska pewność).

    Nic tu nie jest zapisywane. Nadpisanie istniejącego wpisu też jest zmianą do
    ZATWIERDZENIA, nigdy cichą — config jest wspólny dla wszystkich zleceń.
    """
    out = []
    sources = (source_map or {}).get("sources") or {}

    for g in (suggestions or {}).get("group_mappings") or []:
        folder = (g.get("folder") or "").strip()
        src = (g.get("source") or "").strip()
        site = (g.get("site") or "").strip()
        placement = (g.get("placement") or "").strip()
        ad_key = (g.get("adKey") or "").strip()
        conf = g.get("confidence") or 0
        reason = g.get("reason") or ""

        blocked = None
        if not src:
            blocked = "brak nazwy źródła"
        elif not site:
            blocked = "brak Site — reguła bez niego nie ma jak trafić do CM360"
        elif ad_key and ad_key not in ADKEYS:
            blocked = f"nieznany adKey {ad_key!r} (dozwolone: {sorted(ADKEYS)})"
        elif conf < MIN_CONFIDENCE:
            blocked = f"pewność {conf:.0%} poniżej progu {MIN_CONFIDENCE:.0%}"

        cur = sources.get(src)
        if cur is None:
            proposed = {"site": site,
                        "placementByFormat": {placement or "Display": placement or "Display"},
                        "adKey": ad_key or "dimension", **_prov(reason, conf)}
            out.append({"kind": "source", "key": src, "path": f"sources.{src}",
                        "current": None, "proposed": proposed, "reason": reason,
                        "confidence": conf, "blocked": blocked,
                        "label": f"nowe źródło „{src}” (folder {folder or '—'}) → Site {site}"})
            continue

        # Źródło już jest — promujemy tylko BRAKUJĄCY format, nigdy nie podmieniamy Site
        # ani adKey istniejącego wpisu: to zmiana, która po cichu przestawiłaby wszystkie
        # dotychczasowe zlecenia tego źródła.
        if cur.get("site") != site:
            out.append({"kind": "conflict", "key": src, "path": f"sources.{src}.site",
                        "current": cur.get("site"), "proposed": site, "reason": reason,
                        "confidence": conf,
                        "blocked": "źródło już ma inny Site — zmiana ręczna, nie promocja",
                        "label": f"„{src}” ma Site {cur.get('site')}, model proponuje {site}"})
            continue
        pbf = cur.get("placementByFormat") or {}
        if placement and placement not in pbf:
            out.append({"kind": "format", "key": src,
                        "path": f"sources.{src}.placementByFormat.{placement}",
                        "current": None, "proposed": placement, "reason": reason,
                        "confidence": conf, "blocked": blocked,
                        "label": f"nowy format „{placement}” w źródle „{src}”"})

    # advertiser: model zna tylko NAZWĘ, więc bez wskazanego id i anchoru nie ma czego zapisać
    guess = (suggestions or {}).get("advertiser_guess")
    if guess:
        known = {r.get("advertiser") for r in (advertiser_map or {}).get("rules") or []}
        if guess not in known:
            out.append({"kind": "advertiser", "key": guess, "path": "rules[]",
                        "current": None,
                        "proposed": {"advertiser": guess, "advertiserId": advertiser_id},
                        "reason": "model rozpoznał advertisera spoza mapy",
                        "confidence": (suggestions or {}).get("confidence") or 0,
                        "blocked": None if advertiser_id else
                                   "brak advertiserId — sama nazwa nie wystarcza do reguły",
                        "label": f"nowy advertiser „{guess}”"})
    return out


def apply_changes(approved, source_map, advertiser_map):
    """Zastosuj ZATWIERDZONE zmiany. Zwraca (source_map, advertiser_map, log).

    Deterministycznie i bez zaskoczeń: wpis `blocked` jest pomijany nawet gdy ktoś go
    poda, bo blokada wynika z danych, nie z opinii użytkownika.
    """
    src = json.loads(json.dumps(source_map))          # kopia — nie mutujemy wejścia
    adv = json.loads(json.dumps(advertiser_map))
    log = []
    for ch in approved or []:
        if ch.get("blocked"):
            log.append({"skipped": ch.get("path"), "why": ch["blocked"]})
            continue
        kind = ch.get("kind")
        if kind == "source":
            src.setdefault("sources", {})[ch["key"]] = ch["proposed"]
            log.append({"added": ch["path"]})
        elif kind == "format":
            entry = src.setdefault("sources", {}).setdefault(ch["key"], {})
            entry.setdefault("placementByFormat", {})[ch["proposed"]] = ch["proposed"]
            log.append({"added": ch["path"]})
        elif kind == "advertiser":
            p = ch["proposed"]
            adv.setdefault("rules", []).append(
                {"anchor": ch.get("anchor") or [], "advertiserId": p.get("advertiserId"),
                 "advertiser": p.get("advertiser"),
                 **_prov(ch.get("reason"), ch.get("confidence"))})
            log.append({"added": f"rules[] {p.get('advertiser')}"})
        else:
            log.append({"skipped": ch.get("path"), "why": f"nieznany rodzaj {kind!r}"})
    return src, adv, log


def save(source_map=None, advertiser_map=None, src_path=SRC_MAP, adv_path=ADV_MAP):
    """Zapisz config, zostawiając kopię `.bak` obok. Jedyna funkcja dotykająca dysku.

    Kopia jest tu dlatego, że config to jedyne miejsce, gdzie decyzja modelu zostaje na
    stałe i zaczyna działać bez nadzoru — cofnięcie musi być trywialne.
    """
    written = []
    for data, path in ((source_map, src_path), (advertiser_map, adv_path)):
        if data is None:
            continue
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        written.append(path)
    return written


def load():
    with open(SRC_MAP, encoding="utf-8") as f:
        src = json.load(f)
    with open(ADV_MAP, encoding="utf-8") as f:
        adv = json.load(f)
    return src, adv


if __name__ == "__main__":
    import sys
    src, adv = load()
    sug = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
    for ch in changes(sug, src, adv):
        mark = "  BLOK" if ch.get("blocked") else "  ok  "
        print(f"{mark} {ch['label']}")
        if ch.get("blocked"):
            print(f"        {ch['blocked']}")
