"""Tests for the proposal builder: standard GDN case + existing-structure merge."""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import build_proposal as B

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "data", "samples")
passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


parsed = parse_zip.parse(os.path.join(SAMPLES, "GDN Citi.zip"))
camp = {"id": "111", "name": "Test camp", "status": "existing"}
line = {"lineNumber": 3, "lpName": "linia3-GDN", "source": "GDN",
        "path": "x/y", "reused": False}

print("standard GDN case (no existing structure -> all new):")
p = B.build_proposal("GDN", parsed, camp, line)
pl = p["placements"][0]
check("site CG_GDN", p["site"]["name"], "CG_GDN")
check("placement Display new", (pl["name"], pl["status"]), ("Display", "new"))
check("6 ads = 6 dimensions", len(pl["ads"]), 6)
check("ad names are dimensions",
      [a["name"] for a in pl["ads"]],
      ["160x600", "300x250", "300x600", "728x90", "750x200", "750x300"])
check("creative name = linia3", pl["ads"][0]["creatives"][0]["name"], "linia3")
check("one creative per ad by default", {len(a["creatives"]) for a in pl["ads"]}, {1})
check("all creatives new", {a["creatives"][0]["status"] for a in pl["ads"]}, {"new"})
check("6 tags", len(p["tags"]), 6)

print("\ndoklejanie: existing CG_GDN/Display, ad 300x250 already has linia3, "
      "ad 160x600 exists without it:")
existing = {"CG_GDN": {"Display": {
    "300x250": ["linia3", "linia1"],   # creative already there -> no-op
    "160x600": ["linia1"],             # ad exists, our creative missing -> add creative
}}}
p2 = B.build_proposal("GDN", parsed, camp, line, existing=existing)
ads = {a["name"]: a for a in p2["placements"][0]["ads"]}
check("site existing", p2["site"]["status"], "existing")
check("placement existing", p2["placements"][0]["status"], "existing")
check("ad 300x250 existing + creative existing (no-op)",
      (ads["300x250"]["status"], ads["300x250"]["creatives"][0]["status"]),
      ("existing", "existing"))
check("ad 160x600 existing + creative NEW (add creative)",
      (ads["160x600"]["status"], ads["160x600"]["creatives"][0]["status"]),
      ("existing", "new"))
check("ad 728x90 NEW + creative NEW (add ad+creative)",
      (ads["728x90"]["status"], ads["728x90"]["creatives"][0]["status"]),
      ("new", "new"))

print("\nwiele creative na jednym Ad (linia4-słońce + linia4-niebo na tym samym wymiarze):")
p3 = B.build_proposal("GDN", parsed, camp, line)
ad0 = p3["placements"][0]["ads"][0]
ad0["creatives"].append({"name": "linia3-niebo", "type": ad0["creatives"][0]["type"],
                         "packaged": False, "source_path": None, "status": "new"})
ad0["creatives"][0]["name"] = "linia3-slonce"
tags_for_ad0 = [t for t in
               [{"site": "CG_GDN", "placement": p3["placements"][0]["name"],
                 "ad": ad0["name"], "creative": c["name"]} for c in ad0["creatives"]]]
check("ad now carries 2 creatives", len(ad0["creatives"]), 2)
check("2 distinct creative names", {c["name"] for c in ad0["creatives"]},
      {"linia3-slonce", "linia3-niebo"})
check("both tag rows derivable for that ad", len(tags_for_ad0), 2)


# --- several landing pages in one order ---------------------------------------
import matcher as M

KONTA = ["indywidualny", "konta"]
BASE = "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/mkonto/"
PROSP, REMKT = BASE + "?utm_medium=prospecting", BASE + "?utm_medium=remarketing"
LINES = M.resolve_lines([PROSP, REMKT], KONTA, "GDN", [])
DISCS = M.lp_discriminators([PROSP, REMKT], KONTA)

print("\ndwa LP, zip BEZ podziału na foldery -> te same materiały pod obie strony:")
pm = B.build_proposal("GDN", parsed, camp, lines=LINES)
plm = pm["placements"][0]
check("obie linie w propozycji",
      [l["lpName"] for l in pm["lines"]],
      ["linia1-GDN-prospecting", "linia1-GDN-remarketing"])
check("line = pierwsza linia (zgodność ze starym kontraktem)",
      pm["line"]["lpName"], "linia1-GDN-prospecting")
check("nadal jeden placement", len(pm["placements"]), 1)
check("nadal 6 adów (wymiary)", len(plm["ads"]), 6)
check("każdy ad ma 2 creative (po jednym na LP)",
      {len(a["creatives"]) for a in plm["ads"]}, {2})
check("creative niesie WŁASNE LP (orkiestrator je utworzy i zarejestruje)",
      [(c["name"], c["lpName"], c["lpUrl"]) for c in plm["ads"][0]["creatives"]],
      [("linia1-prospecting", "linia1-GDN-prospecting", PROSP),
       ("linia1-remarketing", "linia1-GDN-remarketing", REMKT)])
check("12 tagów = 6 adów × 2 LP", len(pm["tags"]), 12)

print("\ndwa LP + zip z folderami prospecting/ i remarketing/ -> materiały rozdzielone:")
# exactly what parse_zip returns for such a zip: `remarketing` is a GROUP_KEYWORD so it
# lands in `group`, `prospecting` is not so it lands in `variant`
def _u(dim, folder, grp):
    return {"dimension": dim, "variant": folder, "card_index": None, "type": "image",
            "packaged": False, "source_path": f"{folder}/{dim}", "group": grp}


parsed_split = {
    "format_hint": "Display", "warnings": [],
    "groups": [{"name": "remarketing", "source_hint": None, "n_entries": 2}],
    "units": [_u("300x250", "prospecting", None), _u("160x600", "prospecting", None),
              _u("300x250", "remarketing", "remarketing"),
              _u("160x600", "remarketing", "remarketing")],
}
FM = M.match_folders_to_lps(["prospecting", "remarketing"], DISCS)
check("oba foldery przypisane do LP", FM["map"], {"prospecting": 0, "remarketing": 1})
ps = B.build_proposal("GDN", parsed_split, camp, lines=LINES, folder_match=FM)
check("folder przypisany do LP NIE tworzy osobnego placementu",
      [pl["name"] for pl in ps["placements"]], ["Display"])
ads_s = {a["name"]: a for a in ps["placements"][0]["ads"]}
check("2 ady (wymiary z obu folderów zwinięte)", sorted(ads_s), ["160x600", "300x250"])
check("300x250 ma creative dla obu LP",
      [(c["name"], c["lpName"]) for c in ads_s["300x250"]["creatives"]],
      [("linia1-prospecting", "linia1-GDN-prospecting"),
       ("linia1-remarketing", "linia1-GDN-remarketing")])
check("każdy creative wskazuje materiał z WŁASNEGO folderu",
      [c["source_path"] for c in ads_s["300x250"]["creatives"]],
      ["prospecting/300x250", "remarketing/300x250"])
check("4 tagi = 2 ady × 2 LP", len(ps["tags"]), 4)

print("\nzip mieszany: foldery LP + prawdziwy folder formatu (screening):")
parsed_mixed = {
    "format_hint": "Display", "warnings": [],
    "groups": [{"name": "remarketing", "source_hint": None, "n_entries": 1},
               {"name": "screening", "source_hint": None, "n_entries": 1}],
    "units": [_u("300x250", "prospecting", None), _u("300x250", "remarketing", "remarketing"),
              _u("300x250", "screening", "screening")],
}
FM2 = M.match_folders_to_lps(["prospecting", "remarketing", "screening"], DISCS)
check("screening nie pasuje do żadnego LP", FM2["unmatched"], ["screening"])
FM2["consumed"] = sorted(FM2["map"])
pmix = B.build_proposal("GDN", parsed_mixed, camp, lines=LINES, folder_match=FM2)
check("dwa placementy: główny + folder formatu",
      sorted(pl["name"] for pl in pmix["placements"]), ["Display", "screening"])
mix = {pl["name"]: pl for pl in pmix["placements"]}
check("materiały LP NIE giną — trafiają na główny placement",
      [(c["name"], c["source_path"]) for c in mix["Display"]["ads"][0]["creatives"]],
      [("linia1-prospecting", "prospecting/300x250"),
       ("linia1-remarketing", "remarketing/300x250")])
check("folder formatu obsługuje oba LP (nie był przypisany do żadnego)",
      [c["name"] for c in mix["screening"]["ads"][0]["creatives"]],
      ["linia1-prospecting", "linia1-remarketing"])
check("4 tagi = 2 placementy × 1 ad × 2 LP", len(pmix["tags"]), 4)

# the user answers "materials in screening/ are for the remarketing page". That says
# WHICH PAGE they serve — it must not also stop Screening from being its own format
# placement. `consumed` therefore stays the automatically matched folders only.
FM3 = dict(FM2, map=dict(FM2["map"], screening=1), unmatched=[])
pmix2 = B.build_proposal("GDN", parsed_mixed, camp, lines=LINES, folder_match=FM3)
mix2 = {pl["name"]: pl for pl in pmix2["placements"]}
check("przypisanie folderu do LP NIE likwiduje jego placementu",
      sorted(mix2), ["Display", "screening"])
check("...ale zawęża go do wskazanego LP",
      [c["name"] for c in mix2["screening"]["ads"][0]["creatives"]], ["linia1-remarketing"])
check("3 tagi = 2 (główny) + 1 (screening)", len(pmix2["tags"]), 3)

print("\ndetekcja formatu pliku (nazwy folderów z prawdziwej paczki klienta):")
GDN_CONF = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config",
                                       "source_map.json"), encoding="utf-8"))["sources"]["GDN"]
check("tryb dla GDN wzięty z configu", B.format_mode(GDN_CONF), "adSuffix")
check("brak bloku fileFormats -> format nie gra roli", B.format_mode({}), "ignore")


def _fmt(folder, atype):
    return B.file_format({"variant": folder, "group": None, "type": atype}, GDN_CONF)


check("„FRC GIF” -> gif", _fmt("FRC GIF", "gif"), "gif")
check("„FRC PNG” -> png", _fmt("FRC PNG", "image"), "png")
check("„HTML FRC” -> html (słowo formatu może być PIERWSZE)", _fmt("HTML FRC", "html5"), "html")
check("„SPÓŁKA JPG” -> png (alias jpg->png, z polskimi znakami)",
      _fmt("SPÓŁKA JPG", "image"), "png")
check("PNG i JPG nierozróżnialne z typu pliku — dlatego czytamy folder",
      (_fmt("FRC PNG", "image"), _fmt("SPÓŁKA JPG", "image")), ("png", "png"))
check("folder bez słowa formatu -> BRAK formatu, mimo znanego typu assetu",
      (_fmt("prospecting", "gif"), _fmt("prospecting", "html5"),
       _fmt("prospecting", "image")), (None, None, None))
check("brak folderu -> brak formatu", _fmt(None, "html5"), None)


def _uf(dim, folder, atype):
    return {"dimension": dim, "variant": folder, "card_index": None, "type": atype,
            "packaged": False, "source_path": f"{folder}/{dim}", "group": None}


PARSED_FMT = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _uf("160x600", "FRC GIF", "gif"), _uf("300x250", "FRC GIF", "gif"),
    _uf("160x600", "FRC PNG", "image"), _uf("160x600", "HTML FRC", "html5"),
    _uf("160x600", "SPÓŁKA JPG", "image")]}
ONE_LINE = {"lineNumber": 8, "lpName": "linia8-GDN", "source": "GDN",
            "path": "x", "reused": False}


def _with_mode(m):
    return {"GDN": dict(GDN_CONF, fileFormats=dict(GDN_CONF["fileFormats"], mode=m))}


print("\ntryb 'adSuffix' — jeden placement, format w nazwie ada:")
pa = B.build_proposal("GDN", PARSED_FMT, camp, ONE_LINE, source_map=_with_mode("adSuffix"))
check("jeden placement", [pl["name"] for pl in pa["placements"]], ["Display"])
check("ady rozróżnione formatem",
      [a["name"] for a in pa["placements"][0]["ads"]],
      ["160x600_gif", "160x600_html", "160x600_png", "300x250_gif"])
check("PNG i JPG w tym samym wymiarze to JEDEN ad (alias jpg->png)",
      len([a for a in pa["placements"][0]["ads"] if a["name"] == "160x600_png"]), 1)

print("\ntryb 'placement' — placement per format (konwencja z CLAUDE.md):")
pp = B.build_proposal("GDN", PARSED_FMT, camp, ONE_LINE, source_map=_with_mode("placement"))
check("trzy placementy wg mapy gif->GIF, html->HTML, png->Display",
      sorted(pl["name"] for pl in pp["placements"]), ["Display", "GIF", "HTML"])
byname = {pl["name"]: [a["name"] for a in pl["ads"]] for pl in pp["placements"]}
check("każdy placement dostaje TYLKO wymiary ze swoich folderów",
      (byname["GIF"], byname["HTML"], byname["Display"]),
      (["160x600", "300x250"], ["160x600"], ["160x600"]))
check("nazwy adów bez sufiksu — format jest już w nazwie placementu",
      all("_" not in a for ads in byname.values() for a in ads), True)

print("\ntryb 'ignore' — format nie gra roli (zachowanie sprzed zmiany):")
pi = B.build_proposal("GDN", PARSED_FMT, camp, ONE_LINE, source_map=_with_mode("ignore"))
check("jeden placement, ady po samych wymiarach",
      ([pl["name"] for pl in pi["placements"]],
       [a["name"] for a in pi["placements"][0]["ads"]]),
      (["Display"], ["160x600", "300x250"]))

print("\npytania sterujące o nieprzypisane foldery:")
check("brak pytania przy jednym LP",
      M.unresolved_lp_folders({"map": {}, "ambiguous": [], "unmatched": ["GIF"]}, 1), [])
check("nic nie pasowało -> zip nie jest dzielony po LP, brak pytań",
      M.unresolved_lp_folders({"map": {}, "ambiguous": [],
                               "unmatched": ["GIF", "HTML"]}, 2), [])
check("część folderów pasowała -> o resztę pytamy",
      [x["folder"] for x in M.unresolved_lp_folders(
          {"map": {"prospecting": 0}, "ambiguous": [], "unmatched": ["screening"]}, 2)],
      ["screening"])
check("folder pasujący do kilku LP -> pytamy zawsze",
      [x["folder"] for x in M.unresolved_lp_folders(
          {"map": {}, "ambiguous": [{"folder": "gdn_rmg", "candidates": [0, 1]}],
           "unmatched": []}, 2)],
      ["gdn_rmg"])
qs = B.build_questions(parsed_split, folder_match={
    "map": {"prospecting": 0}, "ambiguous": [], "unmatched": ["screening"]},
    lines=pm["lines"])
check("pytanie ma id per folder i opcję „wszystkie LP”",
      (qs[0]["id"], [o["value"] for o in qs[0]["options"]], qs[0]["default"]),
      ("lp_folder:screening", ["0", "1", "all"], "all"))

print("\npaczka Meta z DWOMA folderami formatu (statyki/ + karuzela/) — realne zgłoszenie:")
# Zgłoszone: „z tej paczki narzędzie tworzy 2 placementy — karuzela". Przyczyna: `karuzela`
# jest w GROUP_KEYWORDS parsera, więc trafia do `groups`, a `statyki` nie — wpada do
# resztek, których nazwę brano z format_hint CAŁEGO zipa (a ten to „Karuzela", bo słowo
# występuje w nazwach plików). Drugi placement brał surową nazwę folderu, stąd „karuzela"
# małą literą. Teraz nazwę daje folder danej porcji materiałów przez placementByFormat.
def _um(dim, folder, card, grp=None):
    return {"dimension": dim, "variant": folder, "card_index": card, "type": "image",
            "packaged": False, "source_path": f"pack/{folder}", "group": grp}


PARSED_META = {"format_hint": "Karuzela", "warnings": [], "groups": [
    {"name": "karuzela", "source_hint": None, "n_entries": 4}], "units": [
        _um(None, "karuzela", "1", "karuzela"), _um(None, "karuzela", "2", "karuzela"),
        _um("1200x628", "statyki", "1"), _um("1200x628", "statyki", "2"),
        _um("1080x1920", "statyki", "1")]}
META_LINE = {"lineNumber": 3, "lpName": "linia3-FB-lookalike", "source": "FB",
             "path": "x", "reused": False, "creativeName": "linia3-lookalike"}
pmeta = B.build_proposal("Meta", PARSED_META, camp, META_LINE)
check("dwa RÓŻNE placementy, nazwane wg folderów",
      sorted(pl["name"] for pl in pmeta["placements"]), ["Karuzela", "Statyki"])
check("folder formatu tego samego źródła nie udaje obcego źródła",
      {pl["source"] for pl in pmeta["placements"]}, {"Meta"})
check("oba na Site źródła", {pl["site"] for pl in pmeta["placements"]}, {"CG_Facebook"})
by_name = {pl["name"]: pl for pl in pmeta["placements"]}
check("statyki mają swoje wymiary, a nie karty karuzeli",
      sorted(a["name"] for a in by_name["Statyki"]["ads"]),
      ["statyki_1080x1920_1", "statyki_1200x628_1", "statyki_1200x628_2"])
check("karuzela ma karty", sorted(a["name"] for a in by_name["Karuzela"]["ads"]),
      ["karuzela_1", "karuzela_2"])
check("5 tagów = 5 adów × 1 linia", len(pmeta["tags"]), 5)
# folder formatu tego źródła to nie „które źródło kodujemy" — pytanie o grupy odpada,
# a placement nie ma grupy, po której UI mogłoby go ukryć (domyślna odpowiedź na to
# pytanie zaznaczała tylko PIERWSZĄ grupę, więc druga wypadała z drzewa bez słowa)
check("brak pytania o grupy, gdy folder jest formatem źródła",
      [q["id"] for q in pmeta["questions"]], [])
check("placement z folderu formatu nie ma grupy do filtrowania",
      {pl["group"] for pl in pmeta["placements"]}, {None})
PARSED_FOREIGN = dict(PARSED_META, groups=[
    {"name": "karuzela", "source_hint": None, "n_entries": 2},
    {"name": "screening", "source_hint": None, "n_entries": 1}],
    units=PARSED_META["units"] + [_um("300x250", "screening", "1", "screening")])
qf = B.build_proposal("Meta", PARSED_FOREIGN, camp, META_LINE)["questions"]
check("obcy folder (screening) nadal wymaga decyzji — i tylko on",
      [(q["id"], [o["value"] for o in q["options"]]) for q in qf],
      [("groups", ["screening"])])

# nieznany folder resztek zostaje przy nazwie z format_hint — bez tego każda nietypowa
# nazwa folderu robiłaby własny placement
PARSED_UNK = dict(PARSED_META, groups=[], units=[_um("1200x628", "cokolwiek", "1")])
check("folder, którego źródło nie zna jako formatu -> nazwa z format_hint",
      [pl["name"] for pl in B.build_proposal("Meta", PARSED_UNK, camp, META_LINE)["placements"]],
      ["Karuzela"])

# folder przypisany do LANDING PAGE nie tworzy placementu, nawet gdy nazywa się jak format
pmeta_lp = B.build_proposal("Meta", PARSED_UNK, camp, META_LINE,
                            folder_match={"map": {"cokolwiek": 0}, "consumed": ["cokolwiek"]})
check("folder zużyty jako rozróżnienie LP nie dzieli placementów",
      [pl["name"] for pl in pmeta_lp["placements"]], ["Karuzela"])
PARSED_LPFMT = dict(PARSED_META, groups=[], units=[_um("1200x628", "statyki", "1")])
check("...także wtedy, gdy jego nazwa JEST formatem znanym źródłu",
      [pl["name"] for pl in B.build_proposal(
          "Meta", PARSED_LPFMT, camp, META_LINE,
          folder_match={"map": {"statyki": 0}, "consumed": ["statyki"]})["placements"]],
      ["Karuzela"])

print("\nKILKA ŹRÓDEŁ w jednym zleceniu (paczka z folderami GDN/ + Mailing/):")
# drugie źródło świadomie NIE-serwujące: programmatic ma dziś własny model obiektów
# (patrz „tryb serwujący” niżej), a tu sprawdzamy sam kontrakt wielu źródeł
PARSED_2SRC = {"format_hint": "Display", "warnings": [], "groups": [
    {"name": "GDN", "source_hint": "GDN", "n_entries": 2},
    {"name": "Mailing", "source_hint": "Mailing", "n_entries": 2}], "units": [
        _u("300x250", "GDN", "GDN"), _u("160x600", "GDN", "GDN"),
        _u("300x250", "Mailing", "Mailing"),
        _u("970x250", "Mailing", "Mailing")]}
# jedna strona docelowa, LP na każde źródło — ten sam numer linii, inny sufiks
LINES_2SRC = [
    {"lineNumber": 1, "lpName": "linia1-GDN", "creativeName": "linia1", "source": "GDN",
     "path": "a", "reused": False, "url": "https://x/a?utm_source=gdn"},
    {"lineNumber": 1, "lpName": "linia1-Mailing", "creativeName": "linia1",
     "source": "Mailing", "path": "a", "reused": False,
     "url": "https://x/a?utm_source=mailing"},
]
p2 = B.build_proposal("GDN", PARSED_2SRC, camp, lines=LINES_2SRC,
                      sources=["GDN", "Mailing"])
check("każde źródło ma swój placement na SWOIM Site",
      [(pl["name"], pl["site"], pl["source"]) for pl in p2["placements"]],
      [("Display", "CG_GDN", "GDN"), ("Mailing", "mailsales.pl", "Mailing")])
check("lista Site zlecenia, główne pierwsze",
      [s["name"] for s in p2["sites"]], ["CG_GDN", "mailsales.pl"])
check("źródło główne zostaje w `source`, wszystkie w `sources`",
      (p2["source"], p2["sources"]), ("GDN", ["GDN", "Mailing"]))
check("wybrane źródło nie jest już „obcą grupą” — zero pytań",
      [q["id"] for q in p2["questions"]], [])
check("ady rozdzielone po źródłach (Programmatic ma swoje wymiary)",
      [sorted(a["name"] for a in pl["ads"]) for pl in p2["placements"]],
      [["160x600", "300x250"], ["300x250", "970x250"]])
# to jest sedno: creative na placemencie GDN klika w LP GDN, nie w LP programmatic
check("creative bierze LP swojego źródła",
      [[(c["name"], c["lpName"]) for a in pl["ads"] for c in a["creatives"]]
       for pl in p2["placements"]],
      [[("linia1", "linia1-GDN"), ("linia1", "linia1-GDN")],
       [("linia1", "linia1-Mailing"), ("linia1", "linia1-Mailing")]])
check("po jednym creative na ad — LP drugiego źródła się nie doklejają",
      {len(a["creatives"]) for pl in p2["placements"] for a in pl["ads"]}, {1})
check("4 tagi = 4 ady × 1 creative", len(p2["tags"]), 4)
check("tag niesie Site swojego źródła",
      sorted({t["site"] for t in p2["tags"]}), ["CG_GDN", "mailsales.pl"])
# materiały spoza folderów źródeł należą do źródła GŁÓWNEGO
PARSED_LEFT = dict(PARSED_2SRC, units=PARSED_2SRC["units"] + [_u("750x200", "inne", None)])
# ...ale NIE po cichu: materiał z niezidentyfikowanego folderu dostaje własny, filtrowalny
# węzeł i pytanie (patrz „materiały spoza rozpoznanych folderów")
check("resztki poza folderami źródeł idą na Site źródła głównego, ale osobnym węzłem",
      [(pl["name"], pl["site"], pl["group"]) for pl in B.build_proposal(
          "GDN", PARSED_LEFT, camp, lines=LINES_2SRC,
          sources=["GDN", "Mailing"])["placements"]],
      [("Display", "CG_GDN", B.LOOSE_GROUP), ("Display", "CG_GDN", None),
       ("Mailing", "mailsales.pl", None)])
# bez zaznaczenia drugiego źródła zachowanie jak dotąd: obcy folder = pytanie
p1 = B.build_proposal("GDN", PARSED_2SRC, camp, lines=LINES_2SRC[:1])
check("niewybrane źródło zostaje decyzją użytkownika",
      [(q["id"], [o["value"] for o in q["options"]]) for q in p1["questions"]],
      [("groups", ["Mailing"])])
check("...i ląduje na Site źródła głównego, jak przed zmianą",
      sorted({pl["site"] for pl in p1["placements"]}), ["CG_GDN"])

print("\nZESTAWY MATERIAŁÓW `linia1/` + `linia2/` — jedno LP, rozróżnienie na adzie:")
# Ustalone z użytkownikiem: te foldery NIE są stronami docelowymi. Wcześniej parser
# zwijał oba komplety w jeden ad (ta sama nazwa, ten sam wariant) i połowa materiałów
# przepadała bez śladu.
def _us(dim, folder, sset):
    return {"dimension": dim, "variant": folder, "card_index": None, "set_index": sset,
            "type": "html5", "packaged": False,
            "source_path": f"{folder}/linia{sset}/banner_{dim}", "group": folder}


PARSED_SETS = {"format_hint": "Display", "warnings": [], "groups": [
    {"name": "GDN", "source_hint": "GDN", "n_entries": 4}], "units": [
        _us("300x250", "GDN", "1"), _us("300x250", "GDN", "2"),
        _us("160x600", "GDN", "1"), _us("160x600", "GDN", "2")]}
psets = B.build_proposal("GDN", PARSED_SETS, camp, lines=LINES_2SRC[:1], sources=["GDN"])
check("każdy zestaw ma swój ad, oba pod jednym LP",
      [a["name"] for a in psets["placements"][0]["ads"]],
      ["160x600_1", "160x600_2", "300x250_1", "300x250_2"])
check("...i każdy z tą samą jedną linią",
      {c["name"] for a in psets["placements"][0]["ads"] for c in a["creatives"]},
      {"linia1"})
check("4 tagi, nie 2 — nic się nie zwija", len(psets["tags"]), 4)
check("bez folderów zestawów nazwy adów zostają bez sufiksu",
      [a["name"] for a in B.build_proposal("GDN", dict(PARSED_SETS, units=[
          _u("300x250", "GDN", "GDN")]), camp,
          lines=LINES_2SRC[:1])["placements"][0]["ads"]], ["300x250"])

print("\nMATERIAŁY SPOZA ROZPOZNANYCH FOLDERÓW — pytanie, nie ciche wpuszczenie:")
# Życzenie usera po zgłoszeniu z paczki GDN/+Programmatic/+WP/: nic z niezidentyfikowanego
# folderu nie może wejść do struktury bez jego decyzji. Foldery obok rozpoznanych są
# grupami (parser), a to, co nie leży w ŻADNYM rozpoznanym folderze, dostaje pseudo-grupę.
PARSED_LOOSE = {"format_hint": "Display", "warnings": [], "groups": [
    {"name": "GDN", "source_hint": "GDN", "n_entries": 2}], "units": [
        _u("300x250", "GDN", "GDN"), _u("160x600", "GDN", "GDN"),
        _u("970x250", "cośdziwnego", None)]}
pl_loose = B.build_proposal("GDN", PARSED_LOOSE, camp, lines=LINES_2SRC[:1])
q_loose = next(q for q in pl_loose["questions"] if q["id"] == "groups")
check("materiały luzem trafiają do pytania jako osobna pozycja",
      [o["value"] for o in q_loose["options"]], [B.LOOSE_GROUP])
check("...i NIC nie jest zaznaczone domyślnie", q_loose["default"], [])
check("...a ich placement da się odfiltrować (nosi pseudo-grupę)",
      sorted((pl["name"], str(pl["group"])) for pl in pl_loose["placements"]),
      [("Display", "None"), ("Display", B.LOOSE_GROUP)])
gdn_pl = next(pl for pl in pl_loose["placements"] if pl["group"] is None)
check("wymiar luzem nie wchodzi do placementu rozpoznanego źródła",
      sorted(a["name"] for a in gdn_pl["ads"]), ["160x600", "300x250"])
# paczka NIEROZDZIELONA po folderach to normalne materiały zlecenia — bez pytania
PARSED_FLAT = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _u("300x250", "banner-1", None), _u("160x600", "banner-2", None)]}
check("paczka bez podziału na foldery nie pyta o nic",
      [q["id"] for q in B.build_proposal("GDN", PARSED_FLAT, camp,
                                         lines=LINES_2SRC[:1])["questions"]], [])
# folder przypisany do strony docelowej jest już zidentyfikowany — nie pytamy o niego
check("folder zużyty jako rozróżnienie LP nie jest „materiałem luzem”",
      B.loose_units(PARSED_LOOSE, consumed=["cośdziwnego"]), [])

print("\nMAILING — ad na wysyłkę, kreacja i LP na każdy link (wzorzec: gotowe tagi klienta):")
import matcher as MM
CAMP_MAIL = {"id": "36461008", "name": "Household 08-12.2026", "status": "existing"}
PARSED_MAIL = {"format_hint": "Mailing", "warnings": [], "groups": [], "units": [],
               "mailings": [{"file": "index.html", "skippedLinks": ["#"], "links": [
                   "https://www.mbank.pl/",
                   "https://www.mbank.pl/indywidualny/ubezpieczenia/nieruchomosci/",
                   "https://www.mbank.pl/slowniczek"]}]}
MCONF = B.source_conf("Mailing")
check("numer wysyłki z kampanii: mail1 istnieje -> mail2",
      MM.next_mail_number([{"lpName": "mail1-mbank"}, {"lpName": "linia3-GDN"}]), 2)
check("pusta kampania -> mail1", MM.next_mail_number([]), 1)
mlines = B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1)
check("etykiety startują jako a, b, c — automat nie zgaduje, co jest CTA",
      [(l["creativeName"], l["lpName"]) for l in mlines],
      [("mail-1-a", "mail1-a"), ("mail-1-b", "mail1-b"), ("mail-1-c", "mail1-c")])
check("UTM-y doklejone automatycznie",
      mlines[0]["url"],
      "https://www.mbank.pl/?utm_source=mailing&utm_medium=cpc"
      "&utm_campaign=household_08_12_2026")
check("adres z własnym utm_source nie dostaje drugiego",
      B.mailing_lines(dict(PARSED_MAIL, mailings=[{"file": "i.html", "links":
          ["https://x.pl/?utm_source=inne"], "skippedLinks": []}]),
          MCONF, CAMP_MAIL)[0]["url"], "https://x.pl/?utm_source=inne")
mprop = B.build_proposal("Mailing", PARSED_MAIL, CAMP_MAIL, lines=mlines)
pl_m = mprop["placements"][0]
check("Site i placement z konwencji mailingowej",
      (pl_m["site"], pl_m["name"], pl_m["compatibility"]),
      ("mailsales.pl", "Mailing", "DISPLAY"))
check("JEDEN ad na wysyłkę", [a["name"] for a in pl_m["ads"]], ["mail-1"])
check("kreacje na tym adzie, każda z własnym LP",
      [(c["name"], c["lpName"]) for c in pl_m["ads"][0]["creatives"]],
      [("mail-1-a", "mail1-a"), ("mail-1-b", "mail1-b"), ("mail-1-c", "mail1-c")])
check("tagi = ad × kreacje", len(mprop["tags"]), 3)
check("mailing nie pyta o grupy/formaty (nie ma wymiarów)", mprop["questions"], [])
check("linki bez adresu widoczne w propozycji",
      mprop["mailings"][0]["skippedLinks"], ["#"])

# Zgłoszone: główny button wysyłki ma w kreacji zaślepkę `#`, więc wypadał ze struktury,
# a to najważniejsza kreacja. Adres ze zlecenia (pole „Adresy LP") jest właśnie jego.
MAIN = ("https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/szkola-8/"
        "?utm_source=mailing&utm_medium=cpc&utm_campaign=nnw_08_26")
with_cta = B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1, main_url=MAIN)
check("zaślepka `#` + adres ze zlecenia -> dochodzi wiersz CTA",
      [(l["label"], l["creativeName"], l["lpName"]) for l in with_cta][-1:],
      [("CTA", "mail-1-CTA", "mail1-CTA")])
check("CTA dostaje adres ze zlecenia, bez drugich UTM-ów",
      with_cta[-1]["url"], MAIN)
check("...a etykiety linków z paczki się NIE przesuwają",
      [l["label"] for l in with_cta], ["a", "b", "c", "CTA"])
# bez zaślepki nie dokładamy nic — wszystkie buttony mają swoje adresy
NO_SKIP = dict(PARSED_MAIL, mailings=[dict(PARSED_MAIL["mailings"][0], skippedLinks=[])])
check("brak zaślepki -> brak dodatkowego wiersza",
      len(B.mailing_lines(NO_SKIP, MCONF, CAMP_MAIL, start_no=1, main_url=MAIN)), 3)
check("zaślepka bez adresu w zleceniu -> też nic nie dokładamy (nie ma czego)",
      len(B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1)), 3)
# nazwa nadana przez usera wygrywa nad domyślnym CTA
check("etykietę CTA można nadpisać",
      B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1, main_url=MAIN,
                      override={"1": [{}, {}, {}, {"label": "glowny"}]})[-1]["lpName"],
      "mail1-glowny")
# poprawki użytkownika: nazwy linków + dopisany CTA, którego w paczce nie było (`#`)
OVR = {"1": [{"label": "mbank"}, {"label": "regulamin"}, {"label": "slowniczek"},
             {"label": "CTA", "url": "https://www.mbank.pl/lp2/sierpien-2/"}]}
mlines2 = B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1, override=OVR)
check("po nazwaniu linków wychodzi struktura z gotowych tagów klienta",
      sorted(l["creativeName"] for l in mlines2),
      ["mail-1-CTA", "mail-1-mbank", "mail-1-regulamin", "mail-1-slowniczek"])
check("dopisany link niesie swój adres bez UTM-ów, gdy user podał go sam",
      mlines2[3]["url"], "https://www.mbank.pl/lp2/sierpien-2/")
check("...i ma swoje LP", mlines2[3]["lpName"], "mail1-CTA")
# wyczyszczenie etykiety wraca do domyślnej litery, a nie do pustej nazwy; LP bez sufiksu
# (`mail1`, jak dla CTA w gotowych tagach) user ustawia w edytorze linii, gdzie nazwa LP
# i nazwa kreacji są osobnymi polami
check("wyczyszczona etykieta wraca do domyślnej litery",
      B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1,
                      override={"1": [{"label": " "}]})[0]["lpName"], "mail1-a")
# dwie wysyłki w paczce -> dwa ady na jednym placemencie
PARSED_2M = dict(PARSED_MAIL, mailings=PARSED_MAIL["mailings"] + [
    {"file": "mail2/index.html", "links": ["https://www.mbank.pl/y"], "skippedLinks": []}])
l2m = B.mailing_lines(PARSED_2M, MCONF, CAMP_MAIL, start_no=1)
check("druga wysyłka to mail-2 i LP mail2-*",
      [(l["adName"], l["creativeName"], l["lpName"]) for l in l2m][-1:],
      [("mail-2", "mail-2-a", "mail2-a")])
check("...jako drugi ad na TYM SAMYM placemencie",
      [a["name"] for a in B.build_proposal("Mailing", PARSED_2M, CAMP_MAIL,
                                           lines=l2m)["placements"][0]["ads"]],
      ["mail-1", "mail-2"])

print("\nETYKIETY LP przy źródle serwującym (audiencja zamiast słowa klucza):")
LNK = ["u0", "u1", "u2", "u3"]
labels, lname = B.serving_line_labels(LNK[:3], {0: "linia3"}, "Programmatic")
check("trzy adresy programmatica -> audiencje po kolei",
      labels, {0: "default", 1: "prospecting", 2: "retargeting"})
check("słowo klucza przestaje być etykietą, zostaje nazwą linii", lname, "linia3")
check("wskazanie użytkownika ma pierwszeństwo",
      B.serving_line_labels(LNK[:3], {}, "Programmatic",
                            row_audiences={1: "retargeting"})[0][1], "retargeting")
# zgłoszone: przy adresie źródła NIEserwującego nie ma czego wybierać, a licznik
# audiencji nie może przeskakiwać po cudzych wierszach
mix, _ = B.serving_line_labels(LNK, {0: "linia3", 3: "lookalike"}, "Programmatic",
                               row_sources={3: "Meta"})
check("adres Mety zachowuje swoje słowo klucza, audiencji nie dostaje",
      mix, {0: "default", 1: "prospecting", 2: "retargeting", 3: "lookalike"})
mix2, _ = B.serving_line_labels(LNK, {}, "Meta", row_sources={1: "Programmatic",
                                                             3: "Programmatic"})
check("audiencje liczone w obrębie SWOJEGO źródła, nie po wszystkich wierszach",
      mix2, {1: "default", 3: "prospecting"})
check("zlecenie bez źródła serwującego nic nie zmienia",
      B.serving_line_labels(LNK, {0: "lookalike"}, "GDN"), (None, None))

print("\nTRYB SERWUJĄCY (programmatic) — inny model obiektów niż tracking:")
# Odwzorowane z realnego placementu klienta: JEDEN placement z listą wymiarów, kreacja
# nazwana wymiarem, jeden ad `Display` ze wszystkimi kreacjami, LP per audiencja.
# Adów `{wymiar} Default Web Ad` tu nie ma — tworzy je CM i bierze default kampanii.
import datetime
CAMP_SRV = {"id": "9", "name": "household_08-12.2026", "status": "existing"}
def _usrv(dim, sset):
    return {"dimension": dim, "variant": None, "card_index": None, "set_index": sset,
            "type": "html5", "packaged": True, "group": None,
            "source_path": f"pack_programmatic.zip/{dim}"}


PARSED_SRV = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _usrv("300x250", "KV1"), _usrv("970x250", "KV1"), _usrv("300x250", "KV3")]}
LINES_SRV = [{"lineNumber": 1, "lpName": f"linia1-programmatic-{a}", "creativeName": "linia1",
              "source": "programmatic", "label": a, "keyword": "refinans", "path": "a",
              "reused": False, "url": f"https://x/a?aud={a}"}
             for a in ("default", "prospecting", "retargeting")]
psrv = B.build_proposal("Programmatic", PARSED_SRV, CAMP_SRV, lines=LINES_SRV,
                        sources=["Programmatic"], line_label="refinans",
                        today=datetime.date(2026, 8, 17))
check("placement na (zestaw × audiencja), nazwa wg wzorca z configu",
      [pl["name"] for pl in psrv["placements"]],
      ["household_08-12.2026_refinans-KV1_17.08.2026-prospecting",
       "household_08-12.2026_refinans-KV1_17.08.2026-retargeting",
       "household_08-12.2026_refinans-KV3_17.08.2026-prospecting",
       "household_08-12.2026_refinans-KV3_17.08.2026-retargeting"])
check("wymiary zadeklarowane na placemencie, per zestaw",
      [pl["sizes"] for pl in psrv["placements"]],
      [["300x250", "970x250"], ["300x250", "970x250"], ["300x250"], ["300x250"]])
check("jeden ad `Display` na placement",
      {a["name"] for pl in psrv["placements"] for a in pl["ads"]}, {"Display"})
check("kreacja nazwana WYMIAREM, nie linią",
      [c["name"] for c in psrv["placements"][0]["ads"][0]["creatives"]],
      ["300x250", "970x250"])
check("kreacje klikają w LP swojej audiencji",
      [pl["ads"][0]["creatives"][0]["lpName"] for pl in psrv["placements"]],
      ["linia1-programmatic-prospecting", "linia1-programmatic-retargeting",
       "linia1-programmatic-prospecting", "linia1-programmatic-retargeting"])
check("LP `-default` NIE jest użyte w drzewie (idzie na default kampanii)",
      any("default" in (c.get("lpName") or "") for pl in psrv["placements"]
          for a in pl["ads"] for c in a["creatives"]), False)
check("audiencja i zestaw zapisane na węźle (dla writera)",
      [(pl["audience"], pl["set"]) for pl in psrv["placements"]],
      [("prospecting", "KV1"), ("retargeting", "KV1"),
       ("prospecting", "KV3"), ("retargeting", "KV3")])
check("wszystko na Site programmatica",
      {pl["site"] for pl in psrv["placements"]}, {"CG_Programmatic"})
# bez słowa klucza nazwa linii spada na konwencję, a nie na puste miejsce w nazwie
psrv2 = B.build_proposal("Programmatic", PARSED_SRV, CAMP_SRV,
                         lines=[dict(l, keyword=None) for l in LINES_SRV],
                         sources=["Programmatic"], today=datetime.date(2026, 8, 17))
check("brak słowa klucza -> nazwa linii z konwencji",
      psrv2["placements"][0]["name"],
      "household_08-12.2026_linia1-KV1_17.08.2026-prospecting")
# GDN w tej samej paczce zostaje trackingiem — tryb serwujący jest per ŹRÓDŁO
check("tryb serwujący nie rozlewa się na inne źródła",
      [(pl["name"], pl.get("serving")) for pl in B.build_proposal(
          "GDN", PARSED_SRV, camp, lines=LINES_2SRC[:1])["placements"]],
      [("Display", None)])

print("\nskrót źródła w nazwie LP (config, nie zaszyty w kodzie):")
check("Facebook -> FB", B.lp_source("Facebook"), "FB")
check("Meta (alias tego samego Site) -> ten sam skrót", B.lp_source("Meta"), "FB")
check("źródło bez skrótu zostaje jak jest", B.lp_source("GDN"), "GDN")
check("nieznane źródło nie wybucha", B.lp_source("CośNowego"), "CośNowego")
check("skrót + słowo klucza dają nazwę ze zlecenia",
      M.lp_name(3, B.lp_source("Facebook"), M.keyword_label("lookalike")),
      "linia3-FB-lookalike")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
