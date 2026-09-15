"""Testy promocji decyzji AI do configu (`scripts/promote.py`).

Rdzeń jest czysty — bez plików i bez modelu — więc sprawdzamy go wprost. Nacisk na to,
czego promocja NIE MOŻE zrobić: config działa potem bez nadzoru, na wszystkich kolejnych
zleceniach, więc cicha podmiana istniejącej reguły jest groźniejsza niż brak reguły.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import promote

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


SRC = {"sources": {
    "GDN": {"site": "CG_GDN", "placementByFormat": {"Display": "Display"},
            "adKey": "dimension"},
}}
ADV = {"rules": [{"anchor": ["indywidualny", "konta"], "advertiserId": "9080582",
                  "advertiser": "CG Indywidualny - Konta"}]}


def g(**kw):
    base = {"folder": "Screening", "source": "Screening", "site": "CG_Screening",
            "placement": "Screening", "adKey": "dimension", "confidence": 0.95,
            "reason": "folder nazwany jak format"}
    return dict(base, **kw)


print("NOWE ŹRÓDŁO — główny przypadek promocji:")
ch = promote.changes({"group_mappings": [g()]}, SRC, ADV)
check("jedna zmiana do zatwierdzenia", len(ch), 1)
check("rodzaj i ścieżka", (ch[0]["kind"], ch[0]["path"]), ("source", "sources.Screening"))
check("nie blokowana", ch[0]["blocked"], None)
check("proponowany wpis niesie Site, format i adKey",
      {k: ch[0]["proposed"][k] for k in ("site", "placementByFormat", "adKey")},
      {"site": "CG_Screening", "placementByFormat": {"Screening": "Screening"},
       "adKey": "dimension"})
# proweniencja: bez niej nie da się odróżnić reguły człowieka od reguły modelu
check("wpis oznaczony jako pochodzący od AI", ch[0]["proposed"]["_source"], "ai")
check("...z powodem i pewnością",
      (bool(ch[0]["proposed"]["_reason"]), ch[0]["proposed"]["_confidence"]), (True, 0.95))

src2, adv2, log = promote.apply_changes(ch, SRC, ADV)
check("źródło dopisane do configu", src2["sources"]["Screening"]["site"], "CG_Screening")
check("...a wejście NIE zostało zmutowane", "Screening" in SRC["sources"], False)
check("log mówi, co doszło", log, [{"added": "sources.Screening"}])

print("\nCZEGO PROMOCJA NIE ROBI (config działa potem bez nadzoru):")
low = promote.changes({"group_mappings": [g(confidence=0.5)]}, SRC, ADV)
check("niska pewność -> pokazane, ale zablokowane",
      (len(low), bool(low[0]["blocked"])), (1, True))
check("...z czytelnym powodem", "poniżej progu" in low[0]["blocked"], True)
check("zablokowanej zmiany nie stosujemy, nawet gdy ktoś ją zatwierdzi",
      promote.apply_changes(low, SRC, ADV)[0]["sources"].get("Screening"), None)

nosite = promote.changes({"group_mappings": [g(site="")]}, SRC, ADV)
check("brak Site -> zablokowane", bool(nosite[0]["blocked"]), True)
badkey = promote.changes({"group_mappings": [g(adKey="cokolwiek")]}, SRC, ADV)
check("nieznany adKey -> zablokowane", "nieznany adKey" in badkey[0]["blocked"], True)

# najważniejsze: istniejące źródło nie może po cichu zmienić Site
conflict = promote.changes(
    {"group_mappings": [g(source="GDN", site="CG_INNY", placement="Display")]}, SRC, ADV)
check("inny Site dla ISTNIEJĄCEGO źródła to konflikt, nie promocja",
      (conflict[0]["kind"], bool(conflict[0]["blocked"])), ("conflict", True))
check("...i nie rusza configu",
      promote.apply_changes(conflict, SRC, ADV)[0]["sources"]["GDN"]["site"], "CG_GDN")

print("\nISTNIEJĄCE ŹRÓDŁO — promujemy tylko BRAKUJĄCY format:")
same = promote.changes(
    {"group_mappings": [g(source="GDN", site="CG_GDN", placement="Display")]}, SRC, ADV)
check("format, który już jest -> nic do zrobienia", same, [])
newfmt = promote.changes(
    {"group_mappings": [g(source="GDN", site="CG_GDN", placement="Karuzela")]}, SRC, ADV)
check("nowy format -> jedna zmiana",
      (len(newfmt), newfmt[0]["kind"], newfmt[0]["path"]),
      (1, "format", "sources.GDN.placementByFormat.Karuzela"))
src3, _, _ = promote.apply_changes(newfmt, SRC, ADV)
check("format dopisany obok istniejących",
      src3["sources"]["GDN"]["placementByFormat"], {"Display": "Display", "Karuzela": "Karuzela"})

print("\nADVERTISER — sama nazwa nie wystarcza:")
a1 = promote.changes({"advertiser_guess": "CG Nowy Klient", "confidence": 0.9}, SRC, ADV)
check("bez advertiserId -> zablokowane", bool(a1[0]["blocked"]), True)
a2 = promote.changes({"advertiser_guess": "CG Nowy Klient", "confidence": 0.9}, SRC, ADV,
                     advertiser_id="123456")
check("z advertiserId -> do zatwierdzenia", a2[0]["blocked"], None)
_, adv3, _ = promote.apply_changes(a2, SRC, ADV)
check("reguła dopisana z proweniencją",
      (len(adv3["rules"]), adv3["rules"][-1]["advertiserId"], adv3["rules"][-1]["_source"]),
      (2, "123456", "ai"))
check("...a istniejące reguły nietknięte", adv3["rules"][0]["advertiser"],
      "CG Indywidualny - Konta")
known = promote.changes({"advertiser_guess": "CG Indywidualny - Konta"}, SRC, ADV,
                        advertiser_id="9080582")
check("advertiser, który JUŻ jest w mapie -> nic", known, [])

print("\nrzeczy JEDNORAZOWE nie trafiają do configu:")
# `ad_naming`, `lines` i `resolved_questions` dotyczą TEGO zlecenia, nie reguły —
# w configu zostałyby na zawsze i psuły kolejne paczki
check("ad_naming / lines / resolved_questions są ignorowane",
      promote.changes({"ad_naming": [{"unit": "300x250", "adName": "x", "reason": "r"}],
                       "lines": [{"lpUrl": "u", "source": "GDN", "audience": None,
                                  "lpName": "linia1-GDN", "creativeName": "linia1"}],
                       "resolved_questions": [{"id": "groups", "answer": "GDN"}]},
                      SRC, ADV), [])
check("puste sugestie nie wysypują", promote.changes({}, SRC, ADV), [])
check("brak sugestii w ogóle", promote.changes(None, SRC, ADV), [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
