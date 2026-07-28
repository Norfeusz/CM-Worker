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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
