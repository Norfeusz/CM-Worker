"""Tests for the proposal builder: standard GDN case + existing-structure merge."""
import datetime
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

print("\nFORMATY Z TREŚCI ZLECENIA — źródło bez paczki (realny komentarz o WP):")
MSG_WP = ("LP wp.pl (tu będą potrzebne kody pod formaty 970x200, 970x300, 750x300, "
          "750x200, 750x100, 160x600, 300x250, 300x600 i native ad): "
          "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/szkola-2/\n"
          "GDN i programmatic wg paczki.")
check("wymiary i nazwany format przypisane do WYMIENIONEGO źródła",
      B.formats_from_message(MSG_WP, ["GDN", "WP", "Programmatic"]),
      {"WP": ["970x200", "970x300", "750x300", "750x200", "750x100", "160x600",
              "300x250", "300x600", "NativeAd"]})
check("zdanie bez wymiarów nie dokłada nic innym źródłom",
      set(B.formats_from_message(MSG_WP, ["GDN", "WP", "Programmatic"])), {"WP"})
# przy JEDNYM źródle zlecenia nie trzeba go wymieniać; przy kilku zgadywanie jest zakazane
check("jedno źródło -> wymiary bez wzmianki i tak są jego",
      B.formats_from_message("kody pod 300x250 i 750x100", ["WP"]),
      {"WP": ["300x250", "750x100"]})
check("kilka źródeł bez wzmianki -> nic (zgadywanie dokładałoby ady po cichu)",
      B.formats_from_message("kody pod 300x250", ["WP", "GDN"]), {})
check("pusta wiadomość nie wysypuje budowania", B.formats_from_message("", ["WP"]), {})
# całe drzewo dla źródła, które NIE dostało żadnych materiałów
WP_LINE = {"lineNumber": 1, "lpName": "linia1-WP", "source": "WP", "path": "x",
           "reused": False, "creativeName": "linia1", "url": "https://x/"}
pwp = B.build_proposal("WP", {"format_hint": "Display", "warnings": [], "groups": [],
                              "units": []}, camp, WP_LINE, sources=["WP"], message=MSG_WP)
check("placement powstaje mimo braku paczki",
      [(pl["site"], pl["name"]) for pl in pwp["placements"]], [("WP.pl", "Display")])
check("po jednym adzie na format z opisu",
      [a["name"] for a in pwp["placements"][0]["ads"]],
      ["160x600", "300x250", "300x600", "750x100", "750x200", "750x300", "970x200",
       "970x300", "NativeAd"])
check("kreacja jak wszędzie: linia zlecenia",
      {c["name"] for a in pwp["placements"][0]["ads"] for c in a["creatives"]}, {"linia1"})
check("9 tagów", len(pwp["tags"]), 9)
# gdy źródło MA paczkę, formaty z opisu dokładają się do jego placementu, nie tworzą drugiego
pmix2 = B.build_proposal("GDN", parsed, camp, line, sources=["GDN"],
                         message="dorzućcie jeszcze 970x300")
check("format z opisu dokłada się do placementu z paczki",
      [pl["name"] for pl in pmix2["placements"]], ["Display"])
check("...jako dodatkowy ad obok wymiarów z paczki",
      "970x300" in [a["name"] for a in pmix2["placements"][0]["ads"]], True)
check("...i nie gubi żadnego z paczki", len(pmix2["placements"][0]["ads"]), 7)

print("\nZESTAW Z KOMENTARZA — paczka, której nazwa go nie niesie (realny przypadek KV2):")
check("etykieta zestawu odczytana z treści zlecenia",
      B.set_from_message("Materiały z _kv2 analogicznie do pozostałych."), "kv2")
check("...także bez podkreślnika i z odstępem",
      B.set_from_message("to jest KV 2, reszta bez zmian"), "kv2")
check("kilka zestawów w opisie -> nie zgadujemy",
      B.set_from_message("kv1 i kv3 już są, dochodzi reszta"), None)
check("brak wzmianki -> nic", B.set_from_message("kodujemy GDN wg paczki"), None)
check("wymiar nie udaje zestawu", B.set_from_message("kody pod 300x250"), None)
NOSET = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _u("300x250", None, None), _u("160x600", None, None)]}
GDN_LINE = {"lineNumber": 1, "lpName": "linia1-GDN", "source": "GDN", "path": "x",
            "reused": False, "creativeName": "linia1", "url": "https://x/"}
check("zestaw z komentarza trafia do nazw adów",
      sorted(a["name"] for a in B.build_proposal(
          "GDN", NOSET, camp, GDN_LINE, message="materiały z _kv2 jak poprzednio"
      )["placements"][0]["ads"]),
      ["160x600_kv2", "300x250_kv2"])
# nazwany zestaw daje sufiks NAWET gdy w paczce jest tylko jeden — kolejne KV bywają
# trafficowane osobno i później, a `750x100` zderzyłoby się z `750x100_kv1`
ONESET = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    dict(_u("300x250", None, None), set_index="kv1")]}
check("jeden NAZWANY zestaw i tak dostaje sufiks",
      [a["name"] for a in B.build_proposal("GDN", ONESET, camp, GDN_LINE)
       ["placements"][0]["ads"]], ["300x250_kv1"])
# ...ale numeracja porządkowa (`linia2/` -> `2`) nadal tylko przy kilku kompletach
ONENUM = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    dict(_u("300x250", None, None), set_index="1")]}
check("pojedynczy zestaw NUMEROWANY nie dokłada sufiksu",
      [a["name"] for a in B.build_proposal("GDN", ONENUM, camp, GDN_LINE)
       ["placements"][0]["ads"]], ["300x250"])
# paczka z własnym oznaczeniem nie daje się nadpisać komentarzem
check("własny zestaw paczki wygrywa z komentarzem",
      [a["name"] for a in B.build_proposal("GDN", ONESET, camp, GDN_LINE,
                                           message="materiały z _kv2")
       ["placements"][0]["ads"]], ["300x250_kv1"])

print("\nWP dostaje materiały tylko dwiema drogami (ustalenie usera):")
# 1) folder nazwany WP — reszta paczki zostaje przy swoich źródłach
WP_MIX = {"format_hint": "Display", "warnings": [], "groups": [
    {"name": "GDN", "source_hint": "GDN", "n_entries": 1},
    {"name": "WP", "source_hint": "WP", "n_entries": 2}],
    "units": [_u("300x250", "GDN", "GDN"), _u("970x300", "WP", "WP"),
              _u("750x200", "WP", "WP")]}
pwpmix = B.build_proposal("GDN", WP_MIX, camp, WP_LINE, sources=["GDN", "WP"])
check("folder `WP/` -> Site WP, reszta zostaje przy swoim",
      sorted((pl["site"], tuple(sorted(a["name"] for a in pl["ads"])))
             for pl in pwpmix["placements"]),
      [("CG_GDN", ("300x250",)), ("WP.pl", ("750x200", "970x300"))])
# 2) samo źródło WP + paczka z JEDNYM folderem: folder nie staje się grupą (nie ma obok
# czego być obcym), więc materiały idą na źródło zlecenia — jakkolwiek folder się nazywa
WP_ONE = {"format_hint": "Display", "warnings": [], "groups": [],
          "units": [_u("300x250", "Afiliacja", None), _u("970x200", "Afiliacja", None)]}
check("jedno źródło + jeden folder -> wszystko na to źródło",
      [(pl["site"], sorted(a["name"] for a in pl["ads"]))
       for pl in B.build_proposal("WP", WP_ONE, camp, WP_LINE, sources=["WP"])["placements"]],
      [("WP.pl", ["300x250", "970x200"])])
# ...ale afiliacja NIE jest wiązana z WP na sztywno: obok rozpoznanego folderu jest
# zwykłą obcą grupą, o którą narzędzie pyta, a nie cichym materiałem WP
AFI_MIX = dict(WP_MIX, groups=[{"name": "GDN", "source_hint": "GDN", "n_entries": 1},
                               {"name": "Afiliacja", "source_hint": None, "n_entries": 1}],
               units=[_u("300x250", "GDN", "GDN"), _u("970x200", "Afiliacja", "Afiliacja")])
check("afiliacja obok GDN to obca grupa do decyzji, nie materiał WP",
      [q["id"] for q in B.build_proposal("GDN", AFI_MIX, camp, WP_LINE,
                                         sources=["GDN", "WP"])["questions"]],
      ["groups"])

print("\nnierozpoznany ZESTAW materiałów -> ostrzeżenie zamiast cichego scalenia:")
# Dwa komplety tych samych wymiarów w folderach nazwanych inaczej niż `linia{N}`/`KV{N}`.
# Parser nie ma z czego poznać, że to zestawy, a build zwijał je w JEDEN ad i drugi
# komplet znikał bez śladu. Teraz kolizja jest wykrywana tam, gdzie realnie zachodzi.
parsed_dup = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _u("300x250", "wariant_A", None), _u("160x600", "wariant_A", None),
    _u("300x250", "wariant_B", None), _u("160x600", "wariant_B", None)]}
pdup = B.build_proposal("GDN", parsed_dup, camp, line)
check("dwa komplety w jednym adzie -> ostrzeżenie na każdy wymiar",
      len([w for w in pdup["warnings"] if "to samo miejsce" in w]), 2)
check("...ostrzeżenie mówi, KTÓRE materiały kolidują",
      all(f"wariant_A/{d}" in w and f"wariant_B/{d}" in w
          for d, w in zip(["160x600", "300x250"],
                          sorted(x for x in pdup["warnings"] if "to samo miejsce" in x))),
      True)
check("...i podpowiada, jak to rozróżnić",
      "KV1_" in pdup["warnings"][0], True)
# rozpoznany zestaw ma własny sufiks w nazwie ada, więc nic nie koliduje
parsed_sets = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    dict(_u("300x250", "linia1", None), set_index="1", source_path="linia1/300x250"),
    dict(_u("300x250", "linia2", None), set_index="2", source_path="linia2/300x250")]}
psets = B.build_proposal("GDN", parsed_sets, camp, line)
check("rozpoznany zestaw NIE alarmuje",
      [w for w in psets["warnings"] if "to samo miejsce" in w], [])
check("...bo dostaje osobne ady", sorted(a["name"] for a in psets["placements"][0]["ads"]),
      ["300x250_1", "300x250_2"])
# foldery rozdzielone na dwa LP też nie kolidują — każdy karmi INNĄ linię
check("foldery przypisane do różnych LP nie alarmują",
      [w for w in ps["warnings"] if "to samo miejsce" in w], [])

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
# zgłoszone: folder stoi już w nazwie placementu, więc w nazwie ada się dublował
check("statyki mają swoje wymiary, a nie karty karuzeli — i bez nazwy folderu",
      sorted(a["name"] for a in by_name["Statyki"]["ads"]),
      ["1080x1920_1", "1200x628_1", "1200x628_2"])
# karta karuzeli nie ma wymiaru, więc bez wariantu zostałoby samo `1`/`2`
check("karuzela ma karty — tam wariant ZOSTAJE, bo nie ma wymiaru",
      sorted(a["name"] for a in by_name["Karuzela"]["ads"]),
      ["karuzela_1", "karuzela_2"])
check("5 tagów = 5 adów × 1 linia", len(pmeta["tags"]), 5)
# `video` i `animacje` wskazują w mapie Mety TEN SAM placement `Animacje`, więc wariant
# jest tam jedynym rozróżnikiem adów — bez niego oba komplety zlałyby się w jeden ad
PARSED_2F = {"format_hint": "Animacje", "warnings": [], "groups": [], "units": [
    _um("300x250", "video", None), _um("300x250", "animacje", None)]}
p2f = B.build_proposal("Meta", PARSED_2F, camp, META_LINE)
check("dwa foldery na jednym placemencie -> wariant ZOSTAJE w nazwie ada",
      sorted(a["name"] for a in p2f["placements"][0]["ads"]),
      ["animacje_300x250", "video_300x250"])
check("...i nadal jeden placement", [pl["name"] for pl in p2f["placements"]], ["Animacje"])

print("\npaczka Mety z realnego zlecenia NNW — wariant pliku, typ pliku, sufiks zestawu:")
# Odtworzone z gotowego arkusza klienta (Promocja NNW 08-09.2026): wideo idzie na
# placement `Video`, statyki na `Display`, a ad nazywa się ogonem nazwy pliku od wymiaru.
def _umeta(dim, tag, typ, sset):
    return {"dimension": dim, "file_tag": tag, "variant": None, "card_index": None,
            "set_index": sset, "type": typ, "packaged": False, "group": None,
            "source_path": f"kv{sset[-1]}-meta"}


PARSED_NNW = {"format_hint": "Video", "warnings": [], "groups": [], "units": [
    _umeta("1080x1080", "1080x1080-a", "image", "KV1"),
    _umeta("1080x1080", "1080x1080-b", "image", "KV1"),
    _umeta("1080x1080", "1080x1080-kv1", "video", "KV1"),
    _umeta("1200x628", None, "image", "KV1"),
    _umeta("1080x1080", "1080x1080-a", "image", "KV3"),
    _umeta("1080x1080", "1080x1080-kv3", "video", "KV3")]}
pnnw = B.build_proposal("Meta", PARSED_NNW, camp, META_LINE)
byn = {pl["name"]: sorted(a["name"] for a in pl["ads"]) for pl in pnnw["placements"]}
check("mp4 i statyki rozchodzą się na osobne placementy", sorted(byn), ["Display", "Video"])
check("warianty jednego wymiaru to OSOBNE ady, nazwane ogonem pliku",
      byn["Display"], ["1080x1080-a_KV1", "1080x1080-a_KV3", "1080x1080-b_KV1",
                       "1200x628_KV1"])
# plik, który sam niesie oznaczenie zestawu, nie dostaje go drugi raz
check("nazwa niosąca zestaw nie dostaje sufiksu drugi raz",
      byn["Video"], ["1080x1080-kv1", "1080x1080-kv3"])
check("nic nie ginie: 6 plików -> 6 adów", len(pnnw["tags"]), 6)
# paczka jednorodna typem zostaje przy nazwie z format_hint — typ rozstrzyga tylko przy
# mieszance, inaczej nietypowy folder znów robiłby własny placement
PARSED_ONE = dict(PARSED_NNW, units=[u for u in PARSED_NNW["units"] if u["type"] == "video"])
check("jednorodna paczka -> placement nadal z format_hint",
      [pl["name"] for pl in B.build_proposal("Meta", PARSED_ONE, camp, META_LINE)["placements"]],
      ["Animacje"])

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

# `utm_campaign` nie wynika z samej nazwy kampanii — niesie ona ZAKRES miesięcy
# (`08-12.2026`), a w adresie ma stać miesiąc TEJ wysyłki
check("utm_campaign odtwarza wartość z gotowego arkusza klienta",
      MM.utm_campaign_slug("Household 08-12.2026", datetime.date(2026, 8, 27)),
      "household_sierpien")
check("...miesiąc idzie za dniem trafficowania, nie za nazwą kampanii",
      MM.utm_campaign_slug("Household 08-12.2026", datetime.date(2026, 11, 2)),
      "household_listopad")
check("...kilka słów przed datą zostaje w całości",
      MM.utm_campaign_slug("Promocja NNW 08-09.2026", datetime.date(2026, 8, 1)),
      "promocja_nnw_sierpien")
check("...polskie znaki znikają, bo wartość ląduje w adresie",
      MM.utm_campaign_slug("Ubezpieczenia na życie", datetime.date(2026, 12, 1)),
      "ubezpieczenia_na_zycie_grudzien")
check("...nazwa zaczynająca się od cyfry nie daje pustej podstawy",
      MM.utm_campaign_slug("2026 household", datetime.date(2026, 5, 4)),
      "2026_household_maj")
check("...brak nazwy nie wysypuje budowania",
      MM.utm_campaign_slug("", datetime.date(2026, 1, 9)), "kampania_styczen")
check("...dwanaście miesięcy bez diakrytyków", len(MM.PL_MONTHS), 12)
AUG = datetime.date(2026, 8, 27)          # dzień trafficowania — miesiąc idzie do UTM-ów
mlines = B.mailing_lines(PARSED_MAIL, MCONF, CAMP_MAIL, start_no=1, today=AUG)
check("etykiety startują jako a, b, c — automat nie zgaduje, co jest CTA",
      [(l["creativeName"], l["lpName"]) for l in mlines],
      [("mail-1-a", "mail1-a"), ("mail-1-b", "mail1-b"), ("mail-1-c", "mail1-c")])
# `utm_campaign` odtwarza wartość z gotowego arkusza klienta: nazwa kampanii ucięta
# PRZED datą + miesiąc wysyłki słownie. Sama nazwa dawałaby `household_08_12_2026`,
# a w arkuszu stało `household_sierpien`.
check("UTM-y doklejone automatycznie, z miesiącem wysyłki",
      mlines[0]["url"],
      "https://www.mbank.pl/?utm_source=mailing&utm_medium=cpc"
      "&utm_campaign=household_sierpien")
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
# LP wiersza CTA jest bez sufiksu (`mail1`) — tak jest w gotowych tagach klienta;
# kreacja i ad sufiks zachowują (`mail-1-CTA`), użytkownik potwierdził tę różnicę
check("zaślepka `#` + adres ze zlecenia -> dochodzi wiersz CTA",
      [(l["label"], l["creativeName"], l["lpName"]) for l in with_cta][-1:],
      [("CTA", "mail-1-CTA", "mail1")])
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
check("...i ma LP bez sufiksu, bo to CTA", mlines2[3]["lpName"], "mail1")
# wyczyszczenie etykiety wraca do domyślnej litery, a nie do pustej nazwy
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
CAMP_SRV = {"id": "9", "name": "household_08-12.2026", "status": "existing"}
def _usrv(dim, sset):
    return {"dimension": dim, "variant": None, "card_index": None, "set_index": sset,
            "type": "html5", "packaged": True, "group": None,
            "source_path": f"pack_programmatic.zip/{dim}"}


PARSED_SRV = {"format_hint": "Display", "warnings": [], "groups": [], "units": [
    _usrv("300x250", "kv1"), _usrv("970x250", "kv1"), _usrv("300x250", "kv3")]}
LINES_SRV = [{"lineNumber": 1, "lpName": f"linia1-programmatic-{a}", "creativeName": "linia1",
              "source": "programmatic", "label": a, "keyword": "refinans", "path": "a",
              "reused": False, "url": f"https://x/a?aud={a}"}
             for a in ("default", "prospecting", "retargeting")]
psrv = B.build_proposal("Programmatic", PARSED_SRV, CAMP_SRV, lines=LINES_SRV,
                        sources=["Programmatic"], line_label="refinans",
                        today=datetime.date(2026, 8, 17))
# ZESTAW jest nazwą linii w nazwie placementu — tak stoi w gotowych tagach klienta
# (`promocja_nnw_08-09.2026_kv3_11.08.2026-prospecting`), bez słowa klucza obok
check("placement na (zestaw × audiencja), nazwa wg wzorca z configu",
      [pl["name"] for pl in psrv["placements"]],
      ["household_08-12.2026_kv1_17.08.2026-prospecting",
       "household_08-12.2026_kv1_17.08.2026-retargeting",
       "household_08-12.2026_kv3_17.08.2026-prospecting",
       "household_08-12.2026_kv3_17.08.2026-retargeting"])
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
      [("prospecting", "kv1"), ("retargeting", "kv1"),
       ("prospecting", "kv3"), ("retargeting", "kv3")])
check("wszystko na Site programmatica",
      {pl["site"] for pl in psrv["placements"]}, {"CG_Programmatic"})
# bez zestawów nazwą linii jest słowo klucza, a bez niego konwencja `linia{N}`
PARSED_NOSET = dict(PARSED_SRV, units=[dict(u, set_index=None) for u in PARSED_SRV["units"]])
check("brak zestawu -> nazwą linii jest słowo klucza",
      B.build_proposal("Programmatic", PARSED_NOSET, CAMP_SRV, lines=LINES_SRV,
                       sources=["Programmatic"], line_label="refinans",
                       today=datetime.date(2026, 8, 17))["placements"][0]["name"],
      "household_08-12.2026_refinans_17.08.2026-prospecting")
psrv2 = B.build_proposal("Programmatic", PARSED_NOSET, CAMP_SRV,
                         lines=[dict(l, keyword=None) for l in LINES_SRV],
                         sources=["Programmatic"], today=datetime.date(2026, 8, 17))
check("brak słowa klucza -> nazwa linii z konwencji",
      psrv2["placements"][0]["name"],
      "household_08-12.2026_linia1_17.08.2026-prospecting")
# nazwa kampanii w placemencie jest tylko zlowercase'owana ze spacjami na `_` — myślnik
# i kropka daty ZOSTAJĄ, bo tak są w arkuszu klienta (`promocja_nnw_08-09.2026`)
check("surowa nazwa kampanii -> token placementu",
      B.build_proposal("Programmatic", PARSED_SRV,
                       dict(CAMP_SRV, name="Promocja NNW 08-09.2026"), lines=LINES_SRV,
                       sources=["Programmatic"], line_label="refinans",
                       today=datetime.date(2026, 8, 11))["placements"][0]["name"],
      "promocja_nnw_08-09.2026_kv1_11.08.2026-prospecting")
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
