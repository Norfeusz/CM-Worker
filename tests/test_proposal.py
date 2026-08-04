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
      ["linia1-prospecting-GDN", "linia1-remarketing-GDN"])
check("line = pierwsza linia (zgodność ze starym kontraktem)",
      pm["line"]["lpName"], "linia1-prospecting-GDN")
check("nadal jeden placement", len(pm["placements"]), 1)
check("nadal 6 adów (wymiary)", len(plm["ads"]), 6)
check("każdy ad ma 2 creative (po jednym na LP)",
      {len(a["creatives"]) for a in plm["ads"]}, {2})
check("creative niesie WŁASNE LP (orkiestrator je utworzy i zarejestruje)",
      [(c["name"], c["lpName"], c["lpUrl"]) for c in plm["ads"][0]["creatives"]],
      [("linia1-prospecting", "linia1-prospecting-GDN", PROSP),
       ("linia1-remarketing", "linia1-remarketing-GDN", REMKT)])
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
      [("linia1-prospecting", "linia1-prospecting-GDN"),
       ("linia1-remarketing", "linia1-remarketing-GDN")])
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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
