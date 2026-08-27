"""Offline tests for the matching core, using the user's real example URLs."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import matcher as M

UBEZP = ["indywidualny", "ubezpieczenia"]
FIRMY_KRED = ["firmy", "kredyty"]

RULES = [
    {"anchor": ["indywidualny", "ubezpieczenia"], "advertiserId": "9081506",
     "advertiser": "CG Indywidualny - Ubezpieczenia"},
    {"anchor": ["firmy", "kredyty"], "advertiserId": "9067422",
     "advertiser": "CG Firmy - Kredyty"},
    {"anchor": ["indywidualny", "konta"], "advertiserId": "9080582",
     "advertiser": "CG Indywidualny - Konta"},
]

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok
    failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


print("advertiser resolution (segment-based, ignores /lp2/2026/c1/):")
check("ubezpieczenia (with lp2 prefix)",
      M.resolve_advertiser(
          "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/nieruchomosci/znizka/",
          RULES)["advertiserId"], "9081506")
check("firmy/kredyty (no lp2 prefix, with query)",
      M.resolve_advertiser(
          "https://www.mbank.pl/firmy/kredyty/biezace-zarzadzaniem-firma/pozyczka-dla-firm/?kampania=nmlbc&option=port_google",
          RULES)["advertiserId"], "9067422")
check("indywidualny/konta (with utm + fragment)",
      M.resolve_advertiser(
          "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/festiwale/pol-and-rock-festival/?utm_source=meta#poland-rock",
          RULES)["advertiserId"], "9080582")

print("\ncampaign matching (strip advertiser + utm, compare remaining path):")
UBEZP_CAMPS = [
    {"campaignId": "C1", "campaignName": "Nieruchomosci 06.2026",
     "lpName": "linia1-GDN",
     "lpUrl": "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/nieruchomosci/znizka/?utm_source=google"},
]
ranked, new = M.match_campaigns(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/nieruchomosci/krowa/",
    UBEZP, UBEZP_CAMPS)
check("nieruchomosci/krowa -> same campaign (shares 'nieruchomosci')",
      (ranked[0]["campaignId"], ranked[0]["common"], new), ("C1", 1, False))

ranked2, new2 = M.match_campaigns(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/zwierzeta/krowa/",
    UBEZP, UBEZP_CAMPS)
print("\nDOPASOWANIE KAMPANII — sygnały poza dokładną ścieżką (zgłoszenie z żywej sesji):")
UBEZP = ["indywidualny", "ubezpieczenia"]
SZK8 = ("https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/szkola-8/"
        "?utm_source=mailing&utm_medium=cpc&utm_campaign=nnw_08_26")
SZK2 = ("https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/szkola-2/"
        "?utm_source=dgen&utm_medium=cpc&utm_campaign=nnw_08_26")
CAMP_NNW = [{"campaignId": "C1", "campaignName": "NNW", "lpName": "linia1-DemGen",
             "lpUrl": SZK2}]
r_nnw, new_nnw = M.match_campaigns(SZK8, UBEZP, CAMP_NNW)
# po anchorze zostaje JEDEN człon i różni się numerem odsłony, więc dokładne porównanie
# dawało zero i narzędzie proponowało nową kampanię obok istniejącej
check("szkola-8 obok szkola-2 -> TA SAMA kampania, nie nowa", new_nnw, False)
check("...zadecydowało PODOBIEŃSTWO członu, nie parametry adresu",
      r_nnw[0]["why"].startswith("podobny człon ścieżki: szkola-8 ≈ szkola-2"), True)
check("...dokładnego dopasowania nadal nie ma", r_nnw[0]["common"], 0)

# `utm_campaign` NIE jest sygnałem: te same wartości wracają w różnych kampaniach,
# więc jako podstawa dopasowania było zbyt luźne (zgłoszone przez usera)
INNA = [{"campaignId": "CX", "campaignName": "Inna", "lpName": "linia1-GDN",
         "lpUrl": "https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/podroze/"
                  "?utm_source=gdn&utm_campaign=nnw_08_26"}]
check("to samo utm_campaign przy INNEJ stronie nie dopasowuje",
      M.match_campaigns(SZK8, UBEZP, INNA)[1], True)

# dokładna ścieżka zawsze wygrywa w rankingu z samym podobieństwem
r_rank, _ = M.match_campaigns(SZK8, UBEZP, CAMP_NNW + [
    {"campaignId": "C2", "campaignName": "Szkola 8", "lpName": "linia1-GDN",
     "lpUrl": SZK8}])
check("dokładna ścieżka wygrywa z podobieństwem",
      (r_rank[0]["campaignId"], r_rank[0]["why"]), ("C2", "ta sama ścieżka (szkola-8)"))

print("\npróg podobieństwa członu — pary z realnych kampanii:")
check("szkola-8 ≈ szkola-2 (88%) łapie", M._seg_ratio("szkola-8", "szkola-2") >= 0.7, True)
check("szkola-8 ≈ szkola-18 (94%) łapie",
      M._seg_ratio("szkola-8", "szkola-18") >= 0.7, True)
check("household ≈ household-2 (90%) łapie",
      M._seg_ratio("household", "household-2") >= 0.7, True)
for a, b in [("biedronka", "google"), ("konta", "kredyty"), ("1000", "other"),
             ("przenosze-kredyt", "kredyt-hipoteczny"), ("mkonto", "mkonto-intensive")]:
    check(f"{a} vs {b} NIE łapie ({M._seg_ratio(a, b):.0%})",
          M._seg_ratio(a, b) >= 0.7, False)
check("próg jest jedną stałą, do podkręcenia w jednym miejscu",
      M.SEGMENT_MATCH_RATIO, 0.7)
check("...i da się go podać per wywołanie",
      M.match_campaigns(SZK8, UBEZP, CAMP_NNW, ratio=0.95)[1], True)

check("zwierzeta/krowa -> suggest NEW campaign (no shared segment)",
      (new2, ranked2[0]["common"]), (True, 0))

print("\nline resolution within a campaign (path -> line number, source from UI):")
FIRMY_LPS = [
    {"lpName": "linia1-GDN",
     "lpUrl": "https://www.mbank.pl/firmy/kredyty/biezace-zarzadzaniem-firma/pozyczka-dla-firm/?kampania=nmlbc&option=port_google&sprzedawca=gdn_nml_bc_rmg"},
]
# same path, different source (facebook) -> same line number, FB suffix
r1 = M.resolve_line(
    "https://www.mbank.pl/firmy/kredyty/biezace-zarzadzaniem-firma/pozyczka-dla-firm/?kampania=nmlbc&option=facebook&sprzedawca=fb_nml_bc_rmg",
    FIRMY_KRED, "FB", FIRMY_LPS)
check("same path + FB -> linia1-FB (reused line 1)",
      (r1["lineNumber"], r1["reused"], r1["lpName"]), (1, True, "linia1-FB"))

# different path, same campaign -> next line number
r2 = M.resolve_line(
    "https://www.mbank.pl/firmy/kredyty/biezace-zarzadzaniem-firma/sprzedaz-firmy/?kampania=nmlbc&option=facebook&sprzedawca=fb_nml_bc_rmg",
    FIRMY_KRED, "FB", FIRMY_LPS)
check("new path + FB -> linia2-FB (new line 2)",
      (r2["lineNumber"], r2["reused"], r2["lpName"]), (2, False, "linia2-FB"))

print("\nline conflict detection (same path+source, different query -> ASK):")
YOUNG = ["indywidualny", "konta"]
young_lps = [{"lpName": "linia5-GDN",
              "lpUrl": "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/young-under/google/300/?kampania=gdn_young_13&sprzedawca=gdn_rmg_young_13_{device}"}]
# same path + same source (GDN), only `sprzedawca` differs -> conflict
c1 = M.detect_line_conflict(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/young-under/google/300/?kampania=gdn_young_13&sprzedawca=gdn_young_13_{device}",
    YOUNG, "GDN", young_lps)
check("sprzedawca differs -> conflict=True", c1["conflict"], True)
# different path -> no conflict
c2 = M.detect_line_conflict(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/standard/google/300/?sprzedawca=x",
    YOUNG, "GDN", young_lps)
check("different path -> conflict=False", c2["conflict"], False)

print("\nkonwencja nazw: linia# -> ŹRÓDŁO -> słowo rozróżniające (o ile jest):")
check("LP bez etykiety", M.lp_name(1, "Facebook"), "linia1-Facebook")
check("LP z etykietą — źródło ZAWSZE przed etykietą",
      M.lp_name(1, "Facebook", "lookalike"), "linia1-Facebook-lookalike")
check("creative nie nosi źródła", M.creative_name(1, "lookalike"), "linia1-lookalike")
check("creative bez etykiety", M.creative_name(7), "linia7")
check("rozbiór nazwy z etykietą", M.split_lp_name("linia1-Facebook-lookalike"),
      (1, "Facebook", "lookalike"))
check("rozbiór nazwy bez etykiety", M.split_lp_name("linia2-GDN"), (2, "GDN", None))
check("etykieta może mieć myślniki — źródłem jest tylko pierwszy segment",
      M.split_lp_name("linia3-GDN-konto-firmowe"), (3, "GDN", "konto-firmowe"))
check("nazwa spoza konwencji -> None", M.split_lp_name("refinans-prospecting"), None)

# Ta kolejność coś NAPRAWIA, nie tylko przestawia: dotąd źródła nie dało się odczytać
# z nazwy z etykietą, bo stało na końcu za nieznaną liczbą segmentów, więc wykrywanie
# konfliktu linii dla takiego LP w ogóle nie działało.
LBL = [{"lpName": "linia5-GDN-prospecting",
        "lpUrl": "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/young/?kampania=a"}]
c3 = M.detect_line_conflict(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/young/?kampania=b",
    YOUNG, "GDN", LBL)
check("konflikt wykryty także dla LP z etykietą (wcześniej przepadał)",
      (c3["conflict"], c3["existingLpName"]), (True, "linia5-GDN-prospecting"))
c4 = M.detect_line_conflict(
    "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/young/?kampania=b",
    YOUNG, "FB", LBL)
check("inne źródło -> to nie ten konflikt", c4["conflict"], False)

print("\nnormalization (folder names vs URL tokens):")
check("Polish diacritics + separators", M.normalize("Materiały_Słońce 300x250"),
      "materialy_slonce_300x250")
check("trims junk edges", M.normalize("  -Prospecting-  "), "prospecting")

print("\nseveral LPs in one order — discriminators:")
KONTA = ["indywidualny", "konta"]
BASE = "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/mkonto/"
PROSP, REMKT = BASE + "?utm_medium=prospecting", BASE + "?utm_medium=remarketing"
d = M.lp_discriminators([PROSP, REMKT], KONTA)
check("same path, utm_medium differs -> query value is the token",
      d, [["prospecting"], ["remarketing"]])
check("a single URL has nothing to distinguish it",
      M.lp_discriminators([PROSP], KONTA), [[]])
d2 = M.lp_discriminators([BASE, BASE.replace("mkonto", "mkonto-intensive")], KONTA)
check("differing path segments become tokens", d2, [["mkonto"], ["mkonto_intensive"]])

print("\nseveral LPs in one order — line numbering (the max_no+1 trap):")
two_new = M.resolve_lines([BASE, BASE.replace("mkonto", "mkonto-intensive")],
                          KONTA, "GDN", [])
check("two NEW paths in one order get DIFFERENT numbers",
      [(l["lineNumber"], l["lpName"]) for l in two_new],
      [(1, "linia1-GDN"), (2, "linia2-GDN")])

variants = M.resolve_lines([PROSP, REMKT], KONTA, "GDN", [])
check("same path, different utm -> ONE line, two labelled LPs",
      [(l["lineNumber"], l["lpName"], l["creativeName"]) for l in variants],
      [(1, "linia1-GDN-prospecting", "linia1-prospecting"),
       (1, "linia1-GDN-remarketing", "linia1-remarketing")])

check("identical links collapse to one LP",
      len(M.resolve_lines([PROSP, PROSP], KONTA, "GDN", [])), 1)

# campaign already holds linia1-GDN at exactly the prospecting URL
EXIST = [{"lpName": "linia1-GDN", "lpUrl": PROSP}]
mixed = M.resolve_lines([PROSP, REMKT], KONTA, "GDN", EXIST)
check("known URL keeps its existing LP name (no duplicate LP for one address)",
      (mixed[0]["lpName"], mixed[0]["creativeName"]), ("linia1-GDN", "linia1"))
check("its sibling is labelled instead",
      (mixed[1]["lineNumber"], mixed[1]["lpName"], mixed[1]["creativeName"]),
      (1, "linia1-GDN-remarketing", "linia1-remarketing"))

# a new line added next to an existing one must not reuse number 1
after = M.resolve_lines([BASE.replace("mkonto", "oszczedzam"),
                         BASE.replace("mkonto", "lokata")], KONTA, "GDN", EXIST)
check("new lines continue after the existing max, without colliding",
      [l["lineNumber"] for l in after], [2, 3])

print("\nJEDEN link kolidujący z istniejącym LP — realny błąd z żywej sesji:")
# Zgłoszone: nowy LP różnił się od linia2-GDN tylko parametrem utm_campaign. Narzędzie
# nie dodało nowej linii — użyło istniejącego LP linia2-GDN i istniejącej kreacji linia2,
# i dopięło ją do wszystkich wymiarów z paczki. Przyczyna: jeden link nie ma rodzeństwa,
# więc lp_discriminators zwraca [] i etykiety nie było skąd wziąć.
KRED = ["indywidualny", "kredyty"]
KBASE = "https://www.mbank.pl/lp2/2026/c1/indywidualny/kredyty/kredyt-hipoteczny/przenosze-kredyt/kwiecien/"
NOWY = KBASE + "?utm_source=gdn&utm_medium=cpc&utm_campaign=refinansowanie2026"
STARY = KBASE + "?utm_source=gdn&utm_medium=cpc&utm_campaign=kampania_gdn"
KLPS = [{"lpName": "linia2-GDN", "lpUrl": STARY}]
r_kol = M.resolve_lines([NOWY], KRED, "GDN", KLPS)[0]
check("kolizja z istniejącym LP -> etykieta z różnicy wobec TEGO LP, nie ciche użycie go",
      (r_kol["lineNumber"], r_kol["lpName"], r_kol["creativeName"]),
      (2, "linia2-GDN-refinansowanie2026", "linia2-refinansowanie2026"))
r_ten = M.resolve_lines([STARY], KRED, "GDN", KLPS)[0]
check("ten sam adres co istniejące LP -> używamy go, bez zbędnej etykiety",
      (r_ten["lpName"], r_ten["creativeName"]), ("linia2-GDN", "linia2"))
check("konflikt nadal zgłaszany, żeby użytkownik mógł wybrać reuse",
      M.detect_line_conflict(NOWY, KRED, "GDN", KLPS)["conflict"], True)

print("\nsłowo klucza podane przez użytkownika (okienko przy każdym adresie LP):")
check("spacje w środku -> myślnik", M.keyword_label(" look alike "), "look-alike")
check("interpunkcja wycięta, wielkość liter zachowana",
      M.keyword_label("Lookalike!"), "Lookalike")
check("puste / same separatory -> brak etykiety",
      (M.keyword_label("   "), M.keyword_label("---"), M.keyword_label(None)),
      (None, None, None))

# Przypadek wprost z zlecenia: ta sama strona, dwa utm_medium, źródło Facebook (FB),
# a nazwy linii mają nieść SŁOWO KLUCZA, nie surową wartość utm.
BIEDR = "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/standard/biedronka/other/"
LOOK = BIEDR + "?utm_source=facebook&utm_medium=lookalike"
RETG = BIEDR + "?utm_source=facebook&utm_medium=remarketing"
FBLPS = [{"lpName": "linia1-FB", "lpUrl": BASE},
         {"lpName": "linia2-FB", "lpUrl": BASE.replace("mkonto", "lokata")}]
kw = M.resolve_lines([LOOK, RETG], KONTA, "FB", FBLPS,
                     keywords={0: "lookalike", 1: "remarketing"})
check("dwa adresy + dwa słowa klucza -> jedna linia, nazwy ze słów klucza",
      [(l["lineNumber"], l["lpName"], l["creativeName"]) for l in kw],
      [(3, "linia3-FB-lookalike", "linia3-lookalike"),
       (3, "linia3-FB-remarketing", "linia3-remarketing")])

# to jest cały sens tego pola: etykieta z utm_campaign wychodziła za długa
# (linia2-GDN-refinansowanie2026), a użytkownik chciał `refinans`
one = M.resolve_lines([NOWY], KRED, "GDN", KLPS, keywords={0: "refinans"})[0]
check("słowo klucza wygrywa z etykietą wyprowadzoną z adresu",
      (one["lpName"], one["creativeName"]), ("linia2-GDN-refinans", "linia2-refinans"))

# jeden link bez kolizji nie potrzebuje etykiety, ale skoro user ją podał — jest
solo = M.resolve_lines([PROSP], KONTA, "GDN", [], keywords={0: "wiosna"})[0]
check("pojedynczy adres bez rodzeństwa i bez kolizji też dostaje słowo klucza",
      (solo["lpName"], solo["creativeName"]), ("linia1-GDN-wiosna", "linia1-wiosna"))

half = M.resolve_lines([PROSP, REMKT], KONTA, "GDN", [], keywords={0: "wiosna"})
check("słowo klucza tylko przy jednym adresie -> drugi zostaje przy etykiecie z URL",
      [l["lpName"] for l in half], ["linia1-GDN-wiosna", "linia1-GDN-remarketing"])

check("klucze przychodzące z JSON-a jako tekst też działają",
      M.resolve_lines([PROSP], KONTA, "GDN", [], keywords={"0": "wiosna"})[0]["lpName"],
      "linia1-GDN-wiosna")
check("puste słowo klucza nie kasuje etykiety automatycznej",
      [l["lpName"] for l in M.resolve_lines([PROSP, REMKT], KONTA, "GDN", [],
                                            keywords={0: "  "})],
      ["linia1-GDN-prospecting", "linia1-GDN-remarketing"])

# LP o tym adresie już jest w kampanii: przemianowanie zrobiłoby DRUGIE LP na ten sam
# adres (_ensure_lp szuka po nazwie), więc słowo klucza jest odrzucane — ale jawnie
ign = M.resolve_lines([STARY], KRED, "GDN", KLPS, keywords={0: "refinans"})[0]
check("istniejące LP zachowuje swoją nazwę mimo słowa klucza",
      (ign["lpName"], ign["creativeName"]), ("linia2-GDN", "linia2"))
check("...i zgłasza, że słowo klucza pominięto", ign["keywordIgnored"], True)
check("bez słowa klucza nie ma czego zgłaszać",
      M.resolve_lines([STARY], KRED, "GDN", KLPS)[0]["keywordIgnored"], False)

print("\nKILKA ŹRÓDEŁ w jednym zleceniu — LP per źródło, numer linii ten sam:")
# jeden adres i dwa źródła: dwa LP na TYM SAMYM adresie, bo źródło jest częścią nazwy
one_addr = M.resolve_lines([BASE, BASE], KONTA, "GDN", [],
                           sources={1: "Programmatic"})
check("ten sam adres pod dwoma źródłami nie zwija się do jednego LP",
      [(l["lineNumber"], l["lpName"], l["creativeName"]) for l in one_addr],
      [(1, "linia1-GDN", "linia1"), (1, "linia1-Programmatic", "linia1")])
check("identyczny (adres, źródło) nadal zwija się do jednego",
      len(M.resolve_lines([BASE, BASE], KONTA, "GDN", [])), 1)

# typowy przypadek: adres per źródło, różnią się tylko parametrem
GDNU = BASE + "?utm_source=gdn"
PRGU = BASE + "?utm_source=programmatic"
per_src = M.resolve_lines([GDNU, PRGU], KONTA, "GDN", [], sources={1: "Programmatic"})
check("adres per źródło -> jedna linia, dwa LP, ŻADNYCH etykiet z utm_source",
      [(l["lineNumber"], l["lpName"]) for l in per_src],
      [(1, "linia1-GDN"), (1, "linia1-Programmatic")])
check("...bo rodzeństwem jest tylko wpis o tym samym numerze I źródle",
      [l["labelled"] for l in per_src], [False, False])

# to samo rodzeństwo W OBRĘBIE jednego źródła nadal wymaga etykiet (regresja)
same_src = M.resolve_lines([PROSP, REMKT, PROSP], KONTA, "GDN", [],
                           sources={2: "Programmatic"})
check("dwa adresy jednego źródła -> etykiety; trzeci wpis (inne źródło) bez etykiety",
      [l["lpName"] for l in same_src],
      ["linia1-GDN-prospecting", "linia1-GDN-remarketing", "linia1-Programmatic"])

# LP tego adresu istnieje, ale pod INNYM źródłem — nie wolno go użyć dla naszego
FBLP = [{"lpName": "linia2-FB", "lpUrl": PROSP}]
xsrc = M.resolve_lines([PROSP], KONTA, "GDN", FBLP)[0]
check("istniejące LP innego źródła nie jest przechwytywane — numer linii tak, nazwa nie",
      (xsrc["lineNumber"], xsrc["lpName"]), (2, "linia2-GDN"))
check("LP tego samego źródła nadal jest używane",
      M.resolve_lines([PROSP], KONTA, "FB", FBLP)[0]["lpName"], "linia2-FB")

print("\ndedupe_links — pozycje, po których keyowane jest wszystko dalej:")
check("powtórzony adres znika, słowa klucza zostają przy swoich adresach",
      M.dedupe_links([PROSP, REMKT, PROSP], ["lookalike", "remarketing", None]),
      ([PROSP, REMKT], {0: "lookalike", 1: "remarketing"}, {}))
check("słowo klucza podane przy DRUGIM wystąpieniu adresu nie ginie",
      M.dedupe_links([PROSP, PROSP], [None, "wiosna"]), ([PROSP], {0: "wiosna"}, {}))
check("puste adresy i puste słowa wypadają",
      M.dedupe_links(["  ", PROSP, ""], [None, "  "]), ([PROSP], {}, {}))
check("krótsza lista słów klucza niż adresów nie wysypuje się",
      M.dedupe_links([PROSP, REMKT], ["wiosna"]), ([PROSP, REMKT], {0: "wiosna"}, {}))
check("słowa klucza mogą przyjść jako słownik (jak folderMap)",
      M.dedupe_links([PROSP, REMKT], {"1": "remarketing"}),
      ([PROSP, REMKT], {1: "remarketing"}, {}))
check("ten sam adres pod DWOMA źródłami to dwa wiersze, nie duplikat",
      M.dedupe_links([PROSP, PROSP], None, ["GDN", "Programmatic"]),
      ([PROSP, PROSP], {}, {0: "GDN", 1: "Programmatic"}))
check("ten sam adres pod TYM SAMYM źródłem nadal się zwija",
      M.dedupe_links([PROSP, PROSP], None, ["GDN", "GDN"]), ([PROSP], {}, {0: "GDN"}))

print("\nseveral LPs in one order — folder -> LP matching:")
fm = M.match_folders_to_lps(["prospecting", "remarketing"], d)
check("folder names matching utm values", fm["map"], {"prospecting": 0, "remarketing": 1})
fm2 = M.match_folders_to_lps(["Prospecting", "Remarketing_GDN"], d)
check("case and suffix tolerated (containment both ways)",
      fm2["map"], {"Prospecting": 0, "Remarketing_GDN": 1})
fm3 = M.match_folders_to_lps(["GIF", "HTML", "PNG"], d)
check("format folders are NOT LP folders -> unmatched, left to the placement rules",
      (fm3["map"], fm3["unmatched"]), ({}, ["GIF", "HTML", "PNG"]))

# real delivery: folder names combine the PRODUCT (which page) with the FILE FORMAT,
# and the product is only a WORD inside the LP path — no containment either way
FIRMY = ["firmy", "konta"]
REAL = ["https://www.mbank.pl/lp2/2026/c1/firmy/konta/firmootwieracz/google/czerwiec/zakladanie-firmy/?sprzedawca=gdn_wizerunek_{device}",
        "https://www.mbank.pl/lp2/2026/c1/firmy/konta/firmowe/google/czerwiec/konto/?sprzedawca=gdn_wizerunek_{device}",
        "https://www.mbank.pl/lp2/2026/c1/firmy/konta/firmowe/google/czerwiec/konto-spolka/?sprzedawca=gdn_wizerunek_{device}"]
rd = M.lp_discriminators(REAL, FIRMY)
check("identyczny sprzedawca w każdym LP nie jest dyskryminatorem",
      any("wizerunek" in t for toks in rd for t in toks), False)
rfm = M.match_folders_to_lps(
    ["FRC GIF", "FRC PNG", "HTML FRC", "HTML KONTO FIRMOWE", "HTML SPÓŁKA",
     "KONTO FIRMOWE GIF", "KONTO FIRMOWE PNG", "SPÓŁKA GIF", "SPÓŁKA JPG"], rd)
check("SPÓŁKA * -> LP z konto-spolka (wspólne SŁOWO, nie zawieranie)",
      {f: i for f, i in rfm["map"].items() if "SP" in f},
      {"HTML SPÓŁKA": 2, "SPÓŁKA GIF": 2, "SPÓŁKA JPG": 2})
check("FRC * -> nierozstrzygnięte (skrót od firmootwieracz jest niewyprowadzalny)",
      sorted(f for f in rfm["unmatched"] if "FRC" in f),
      ["FRC GIF", "FRC PNG", "HTML FRC"])
check("KONTO FIRMOWE * -> niejednoznaczne (pasuje i do /konto/, i do /konto-spolka/)",
      sorted(a["folder"] for a in rfm["ambiguous"]),
      ["HTML KONTO FIRMOWE", "KONTO FIRMOWE GIF", "KONTO FIRMOWE PNG"])

AMB = [BASE + "?sprzedawca=gdn_rmg_a", BASE + "?sprzedawca=gdn_rmg_b"]
fm4 = M.match_folders_to_lps(["gdn_rmg"], M.lp_discriminators(AMB, KONTA))
check("folder fitting both LPs -> ambiguous, never guessed",
      (fm4["map"], fm4["ambiguous"]), ({}, [{"folder": "gdn_rmg", "candidates": [0, 1]}]))

print("\nlabel fallback (URL token unusable -> matched folder name):")
check("bare id is not a readable label", M.lp_label(["12345"]), None)
check("falls back to the folder", M.lp_label(["12345"], "Prospecting"), "prospecting")
check("hash-like id is not readable", M.lp_label(["a3f9c081"], None), None)
ids = M.resolve_lines([BASE + "?utm_content=12345", BASE + "?utm_content=67890"],
                      KONTA, "GDN", [], labels={0: "prospecting", 1: "remarketing"})
check("unreadable URL tokens + folder labels -> usable LP names",
      [l["lpName"] for l in ids],
      ["linia1-GDN-prospecting", "linia1-GDN-remarketing"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
