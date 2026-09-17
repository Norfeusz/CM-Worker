"""Offline test of orchestrator decision branches (dry-run touches no API)."""
import datetime
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import build_proposal as B
from orchestrate import Orchestrator

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
camp = {"id": "C1", "name": "demo", "status": "existing",
        "startDate": "2026-06-01", "endDate": "2026-08-31"}
line = {"lineNumber": 2, "lpName": "linia2-GDN", "source": "GDN",
        "path": "nieruchomosci/promocja", "reused": False}
proposal = B.build_proposal("GDN", parsed, camp, line,
                            target_url="https://x/nieruchomosci/promocja")

# synthetic live state: site+placement+two dim-ads already exist
state = {
    "sites_by_name": {"CG_GDN": "SITE1"},
    "placements": {("CG_GDN", "Display"): "PLC1"},
    "ads": {("CG_GDN", "Display", "160x600"): "AD160",
            ("CG_GDN", "Display", "300x250"): "AD300"},
    "ad_creatives": {("CG_GDN", "Display", "160x600"): {"linia2"},   # -> NO-OP
                     ("CG_GDN", "Display", "300x250"): {"linia1"}},  # -> UPDATE (append)
    "creatives_by_name": {},          # linia2 new -> CREATE
    "lps_by_name": {}, "adv_lp_by_name_url": {},   # LP new -> CREATE
}

orch = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=camp, dry_run=True)
log = orch.run(proposal, state)
by = {(e["kind"], e["name"]): e["action"] for e in log}

print()
check("LP new -> CREATE", by[("landingPage", "linia2-GDN")], "CREATE")
check("site exists -> REUSE", by[("site", "CG_GDN")], "REUSE")
check("creative new -> CREATE", by[("creative", "linia2")], "CREATE")
check("placement exists -> REUSE", by[("placement", "Display")], "REUSE")
check("ad 160x600 has linia2 -> NO-OP", by[("ad", "160x600")], "NO-OP")
check("ad 300x250 missing linia2 -> UPDATE", by[("ad", "300x250")], "UPDATE")
check("ad 300x600 absent -> CREATE", by[("ad", "300x600")], "CREATE")
check("ad 728x90 absent -> CREATE", by[("ad", "728x90")], "CREATE")

print("\nmultiple creatives on ONE ad (linia1+linia2 na istniejącym 300x250; "
      "linia2+linia3 na całkiem nowym 999x999):")
proposal2 = B.build_proposal("GDN", parsed, camp, line,
                             target_url="https://x/nieruchomosci/promocja")
ad300 = next(a for a in proposal2["placements"][0]["ads"] if a["name"] == "300x250")
ad300["creatives"] = [
    {"name": "linia1", "type": "html5", "packaged": False, "source_path": None, "status": "existing"},
    {"name": "linia2", "type": "html5", "packaged": False, "source_path": None, "status": "new"},
]
proposal2["placements"][0]["ads"].append({
    "name": "999x999", "dimension": "999x999", "status": "new", "creatives": [
        {"name": "linia2", "type": "html5", "packaged": False, "source_path": None, "status": "new"},
        {"name": "linia3", "type": "html5", "packaged": False, "source_path": None, "status": "new"},
    ],
})
state2 = dict(state, creatives_by_name={"linia1": "CREID1"})

orch2 = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=camp, dry_run=True)
log2 = orch2.run(proposal2, state2)

e300 = [e for e in log2 if e["kind"] == "ad" and e["name"] == "300x250"]
check("300x250 has 2 log entries (one per creative)", len(e300), 2)
check("300x250 linia1 -> NO-OP",
      next(e["action"] for e in e300 if "linia1" in e["detail"]), "NO-OP")
check("300x250 linia2 -> UPDATE",
      next(e["action"] for e in e300 if "linia2" in e["detail"]), "UPDATE")

e999 = [e for e in log2 if e["kind"] == "ad" and e["name"] == "999x999"]
check("999x999 has 2 log entries (CREATE + append UPDATE)", len(e999), 2)
check("999x999 first -> CREATE (with 1st creative)", e999[0]["action"], "CREATE")
check("999x999 second -> UPDATE (append 2nd creative to the new ad)", e999[1]["action"], "UPDATE")

print("\nper-creative custom LP (linia4-słońce + linia4-niebo, own distinct LPs; "
      "a third creative shares the LINE's LP by default):")
proposal3 = B.build_proposal("GDN", parsed, camp, line,
                             target_url="https://x/nieruchomosci/promocja")
ad3 = proposal3["placements"][0]["ads"][0]  # brand-new ad -> exercises the CREATE+append path
ad3["creatives"] = [
    {"name": "linia3-slonce", "type": "html5", "packaged": False, "source_path": None,
     "status": "new", "lpName": "linia3-GDN-slonce", "lpUrl": "https://x/slonce?utm_content=slonce"},
    {"name": "linia3-niebo", "type": "html5", "packaged": False, "source_path": None,
     "status": "new", "lpName": "linia3-GDN-niebo", "lpUrl": "https://x/niebo?utm_content=niebo"},
    {"name": "linia3", "type": "html5", "packaged": False, "source_path": None, "status": "new"},
]
state3 = dict(state, creatives_by_name={}, ad_creatives={})
camp_new = dict(camp)  # no defaultLandingPageId -> first LP processed becomes "as default"

orch3 = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=camp_new, dry_run=True)
log3 = orch3.run(proposal3, state3)

lp_creates = {e["name"] for e in log3 if e["kind"] == "landingPage"}
check("3 distinct LPs resolved (2 custom + 1 shared line LP)", lp_creates,
      {"linia3-GDN-slonce", "linia3-GDN-niebo", "linia2-GDN"})
ad_entries = [e for e in log3 if e["kind"] == "ad" and e["name"] == ad3["name"]]
check("ad has 3 log entries (CREATE + 2 append UPDATEs)", len(ad_entries), 3)
check("slonce creative appended with its OWN LP",
      any("linia3-slonce" in e["detail"] and "linia3-GDN-slonce" in e["detail"] for e in ad_entries), True)
check("niebo creative appended with its OWN LP",
      any("linia3-niebo" in e["detail"] and "linia3-GDN-niebo" in e["detail"] for e in ad_entries), True)
check("plain linia3 creative uses the SHARED line LP",
      any("creative=linia3 " in e["detail"] and "linia2-GDN" in e["detail"] for e in ad_entries) or
      any("append creative linia3 " in e["detail"] and "linia2-GDN" in e["detail"] for e in ad_entries), True)

print("\nNOWA kampania (status=new, brak id): LP linii -> kampania -> site/creative/placement/ad,\n"
      "plus jeden creative z własnym LP (musi trafić na listę stron docelowych osobno):")
camp_brand_new = {"id": None, "name": "Household 09.2026 - nowa", "status": "new"}
proposal4 = B.build_proposal("GDN", parsed, camp_brand_new, line,
                             target_url="https://x/nieruchomosci/promocja")
proposal4["placements"][0]["ads"][0]["creatives"].append(
    {"name": "linia2-niebo", "type": "html5", "packaged": False, "source_path": None,
     "status": "new", "lpName": "linia2-GDN-niebo", "lpUrl": "https://x/niebo"})
state4 = {"sites_by_name": {"CG_GDN": "SITE1"}, "placements": {}, "ads": {},
          "ad_creatives": {}, "creatives_by_name": {},
          "lps_by_name": {}, "adv_lp_by_name_url": {}}

orch4 = Orchestrator(svc=None, profile_id="P", advertiser_id="A",
                     campaign=camp_brand_new, dry_run=True)
log4 = orch4.run(proposal4, state4)
kinds = [e["kind"] for e in log4]

print()
check("kampania -> CREATE", [e["action"] for e in log4 if e["kind"] == "campaign"], ["CREATE"])
check("LP linii powstaje PRZED kampanią (wymagany defaultLandingPageId)",
      kinds.index("landingPage") < kinds.index("campaign"), True)
check("kampania powstaje PRZED placementem",
      kinds.index("campaign") < kinds.index("placement"), True)
check("LP linii nie jest rejestrowana osobno (jest defaultem kampanii)",
      [e["name"] for e in log4 if e["kind"] == "campaign-LP"], ["linia2-GDN-niebo"])
check("start = dziś", orch4.start_date, datetime.date.today().isoformat())
check("koniec = start + 5 lat",
      datetime.date.fromisoformat(orch4.end_date).year
      - datetime.date.fromisoformat(orch4.start_date).year, 5)
check("nowa kampania: everything below it is CREATE (site istnieje -> REUSE)",
      {e["kind"]: e["action"] for e in log4 if e["kind"] in ("site", "creative", "placement")},
      {"site": "REUSE", "creative": "CREATE", "placement": "CREATE"})
check("orkiestrator przyjął id nowej kampanii", orch4.cid, "(new)")

print("\nWIELE LP W JEDNYM ZLECENIU: dwa linki do tej samej kampanii, zip podzielony na\n"
      "foldery prospecting/ i remarketing/ — cała ścieżka zapisu bez zmian:")
import matcher as M

KONTA = ["indywidualny", "konta"]
LPBASE = "https://www.mbank.pl/lp2/2026/c1/indywidualny/konta/mkonto/"
P_URL, R_URL = LPBASE + "?utm_medium=prospecting", LPBASE + "?utm_medium=remarketing"
lines_multi = M.resolve_lines([P_URL, R_URL], KONTA, "GDN", [])
fm_multi = M.match_folders_to_lps(["prospecting", "remarketing"],
                                  M.lp_discriminators([P_URL, R_URL], KONTA))
parsed_multi = {
    "format_hint": "Display", "warnings": [],
    "groups": [{"name": "remarketing", "source_hint": None, "n_entries": 1}],
    "units": [
        {"dimension": "300x250", "variant": "prospecting", "card_index": None,
         "type": "image", "packaged": False, "source_path": "prospecting/300x250",
         "group": None},
        {"dimension": "300x250", "variant": "remarketing", "card_index": None,
         "type": "image", "packaged": False, "source_path": "remarketing/300x250",
         "group": "remarketing"},
    ],
}
proposal5 = B.build_proposal("GDN", parsed_multi, camp, lines=lines_multi,
                             folder_match=fm_multi)
state5 = {"sites_by_name": {"CG_GDN": "SITE1"}, "placements": {}, "ads": {},
          "ad_creatives": {}, "creatives_by_name": {},
          "lps_by_name": {}, "adv_lp_by_name_url": {}}
orch5 = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=dict(camp),
                     dry_run=True)
log5 = orch5.run(proposal5, state5)

print()
lp5 = [e["name"] for e in log5 if e["kind"] == "landingPage"]
check("dokładnie DWA LP — żadnego osieroconego LP „linii” obok nich",
      sorted(lp5), ["linia1-GDN-prospecting", "linia1-GDN-remarketing"])
check("drugie LP zarejestrowane na liście stron docelowych kampanii",
      [e["name"] for e in log5 if e["kind"] == "campaign-LP"],
      ["linia1-GDN-prospecting", "linia1-GDN-remarketing"])
check("dwa creative, po jednym na LP",
      sorted(e["name"] for e in log5 if e["kind"] == "creative"),
      ["linia1-prospecting", "linia1-remarketing"])
ad5 = [e for e in log5 if e["kind"] == "ad"]
check("jeden ad 300x250: CREATE + append drugiego creative",
      [(e["name"], e["action"]) for e in ad5],
      [("300x250", "CREATE"), ("300x250", "UPDATE")])
check("każdy creative wskazuje SWOJE LP",
      [("prospecting" in e["detail"], "remarketing" in e["detail"]) for e in ad5],
      [(True, False), (False, True)])
check("2 tagi = 1 ad × 2 LP", len(proposal5["tags"]), 2)

print("\nLP BEZ ADRESU — realna awaria z żywego zapisu: CM360 odrzucił insert bez url\n"
      "(błąd 18112) w ŚRODKU zapisu, gdy kampania i część LP już powstały:")
prop_nourl = B.build_proposal("GDN", parsed, camp, line,
                              target_url="https://x/nieruchomosci/promocja")
ad_nu = prop_nourl["placements"][0]["ads"][0]
ad_nu["creatives"].append(
    {"name": "linia2-refinans", "type": "html5", "packaged": False, "source_path": None,
     "status": "new", "lpName": "linia2-GDN-refinans"})          # lpUrl BRAKUJE
brak = Orchestrator.lp_urls_missing(prop_nourl, state)
check("LP bez adresu zgłoszone przed zapisem", sorted(brak), ["linia2-GDN-refinans"])
check("...z miejscem użycia, żeby wiedzieć któremu creative dopisać adres",
      brak["linia2-GDN-refinans"], [f"Display/{ad_nu['name']}/linia2-refinans"])

# pusty url jest OK, gdy LP już istnieje w kampanii — wtedy _ensure_lp znajdzie je
# po nazwie i niczego nie tworzy
state_ma_lp = dict(state, lps_by_name={"linia2-GDN-refinans": "LP99"})
check("istniejące LP bez podanego url NIE jest problemem",
      Orchestrator.lp_urls_missing(prop_nourl, state_ma_lp), {})

prop_line_nourl = B.build_proposal("GDN", parsed, camp, line)   # bez target_url
gdzie = Orchestrator.lp_urls_missing(prop_line_nourl, state).get("linia2-GDN")
check("LP linii bez adresu też jest łapane", gdzie[0], "LP linii")
check("...i wskazuje wszystkie creative, które z niego korzystają (to skutek, nie osobny błąd)",
      len(gdzie), 1 + len(parsed["units"]))

check("gdy wszystko ma adresy — brak zastrzeżeń",
      Orchestrator.lp_urls_missing(proposal, state), {})

print("\nDRUGIE LP NA TYM SAMYM ADRESIE — zapis pyta o zgodę (LP w CM360 się nie usuwa):")
DUP_URL = "https://x/nieruchomosci/promocja"
prop_dup = B.build_proposal("GDN", parsed, camp, line, target_url=DUP_URL)
state_ma_adres = dict(state, lps_by_name={"linia1-GDN-stara": "LP1"},
                      lp_urls_by_name={"linia1-GDN-stara": DUP_URL})
dup = Orchestrator.duplicate_lp_urls(prop_dup, state_ma_adres)
check("wykrywa, że adres ma już swoją stronę w kampanii",
      {k: v for k, v in dup.items()}, {"linia2-GDN": ["linia1-GDN-stara"]})
check("gdy adres jest wolny — nic do potwierdzania",
      Orchestrator.duplicate_lp_urls(
          prop_dup, dict(state, lps_by_name={}, lp_urls_by_name={})), {})
check("LP, które JUŻ istnieje pod tą nazwą, niczego nie zakłada",
      Orchestrator.duplicate_lp_urls(
          prop_dup, dict(state, lps_by_name={"linia2-GDN": "LP7"},
                         lp_urls_by_name={"linia2-GDN": DUP_URL})), {})
# Parametry ROZRÓŻNIAJĄ strony (utm_source per źródło) — zwijanie ich robiłoby fałszywy
# alarm na każdym zleceniu wieloźródłowym, więc porównanie jest dokładne.
check("ten sam adres z innym parametrem to inna strona, nie duplikat",
      Orchestrator.duplicate_lp_urls(
          prop_dup, dict(state, lps_by_name={"linia1-GDN-stara": "LP1"},
                         lp_urls_by_name={"linia1-GDN-stara": DUP_URL + "?utm_source=gdn"})),
      {})
# DRUGA droga i ta WAŻNIEJSZA: ślad po konwersji. Realny przypadek z 17.09.2026 miał na
# koncie `utm_medium=cpc`, którego nie było w linku ze zlecenia, więc po samych adresach
# wychodziły dwie RÓŻNE strony i bramka milczała — a zapis i tak zakładał drugie LP.
prop_conv = B.build_proposal("GDN", parsed, camp, line, target_url=DUP_URL)
prop_conv["line"]["replacesLp"] = "linia1-GDN-stara"
prop_conv["lines"][0]["replacesLp"] = "linia1-GDN-stara"
check("konwersja jest wykryta MIMO innego zapisu adresu",
      Orchestrator.duplicate_lp_urls(
          prop_conv, dict(state, lps_by_name={"linia1-GDN-stara": "LP1"},
                          lp_urls_by_name={"linia1-GDN-stara": DUP_URL + "&utm_medium=cpc"})),
      {"linia2-GDN": ["linia1-GDN-stara"]})
check("gdy stara strona NIE istnieje w kampanii — nie ma o co pytać",
      Orchestrator.duplicate_lp_urls(
          prop_conv, dict(state, lps_by_name={}, lp_urls_by_name={})), {})
check("gdy nowa nazwa JUŻ istnieje — też nie zakładamy drugiej",
      Orchestrator.duplicate_lp_urls(
          prop_conv, dict(state, lps_by_name={"linia1-GDN-stara": "LP1", "linia2-GDN": "LP2"},
                          lp_urls_by_name={})), {})

check("pusty adres nie jest duplikatem niczego",
      Orchestrator.duplicate_lp_urls(
          B.build_proposal("GDN", parsed, camp, line),
          dict(state, lps_by_name={"x": "LP1"}, lp_urls_by_name={"x": ""})), {})

print("\nPISOWNIA LP: konto klienta miesza wielkość liter w jednej kampanii\n"
      "(35398313: 14x `Linia4-FB-Konto`, ale `linia7-FB-rozchodniak`) — porównanie\n"
      "dokładne tworzyło DRUGIE LP na ten sam adres, a LP w CM360 się nie usuwa:")
check("LP znalezione mimo innej wielkości liter — z nazwą TAKĄ, JAKA JEST na koncie",
      Orchestrator._find_lp({"Linia4-FB-Konto": "LP1"}, "linia4-FB-Konto"),
      ("Linia4-FB-Konto", "LP1"))
check("dokładne trafienie ma pierwszeństwo, gdy stoją obie pisownie",
      Orchestrator._find_lp({"Linia4-FB-Konto": "LP1", "linia4-FB-Konto": "LP2"},
                            "linia4-FB-Konto"),
      ("linia4-FB-Konto", "LP2"))
check("czego nie ma, tego nie ma — nadal CREATE",
      Orchestrator._find_lp({"Linia4-FB-Konto": "LP1"}, "linia5-FB-Ceidg"), (None, None))
state_inna_pisownia = dict(state, lps_by_name={"LINIA2-gdn-REFINANS": "LP99"})
check("...i nie żądamy adresu dla LP, które na koncie jest, tylko pisane inaczej",
      Orchestrator.lp_urls_missing(prop_nourl, state_inna_pisownia), {})

print("\nKILKA ŹRÓDEŁ w jednym zleceniu — o Site decyduje PLACEMENT, nie zlecenie "
      "(paczka z folderami GDN/ + Programmatic/):")
prop_ms = B.build_proposal("GDN", parsed, camp, line,
                           target_url="https://x/nieruchomosci/promocja")
# drugie źródło: własny Site, ten sam numer linii, LP z sufiksem swojego źródła
prop_ms["placements"].append({
    "name": "Display", "group": None, "source": "Programmatic", "site": "CG_Programmatic",
    "compatibility": "DISPLAY", "size": "1x1", "status": "new", "ads": [
        {"name": "300x250", "dimension": "300x250", "status": "new", "creatives": [
            {"name": "linia2", "type": "html5", "packaged": False, "source_path": None,
             "status": "new", "lpName": "linia2-Programmatic",
             "lpUrl": "https://x/nieruchomosci/promocja?utm_source=programmatic"}]}]})
state_ms = dict(state, sites_by_name={"CG_GDN": "SITE1", "CG_Programmatic": "SITE2"})
log_ms = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=camp,
                      dry_run=True).run(prop_ms, state_ms)
check("każdy Site rozstrzygany osobno, główny pierwszy",
      [(e["action"], e["name"]) for e in log_ms if e["kind"] == "site"],
      [("REUSE", "CG_GDN"), ("REUSE", "CG_Programmatic")])
check("placement drugiego źródła powstaje na SWOIM Site",
      [(e["action"], e["detail"]) for e in log_ms if e["kind"] == "placement"],
      [("REUSE", "site=CG_GDN"), ("CREATE", "site=CG_Programmatic")])
check("LP drugiego źródła utworzone obok LP pierwszego",
      sorted(e["name"] for e in log_ms if e["kind"] == "landingPage"),
      ["linia2-GDN", "linia2-Programmatic"])
# ad o TEJ SAMEJ nazwie istnieje na Site pierwszego źródła — nie wolno go tu użyć
ad_ms = [e for e in log_ms if e["kind"] == "ad" and e["name"] == "300x250"]
check("ad 300x250 na drugim Site to CREATE, mimo że ta nazwa istnieje na CG_GDN",
      [e["action"] for e in ad_ms], ["UPDATE", "CREATE"])
check("...i klika w LP swojego źródła",
      "linia2-Programmatic" in ad_ms[-1]["detail"], True)

print("\nGAŁĄŹ SERWUJĄCA (programmatic): upload -> kreacja DISPLAY -> placement -> ad:")
# paczka budowana w locie, żeby test nie zależał od plików klienta
import io as _io, tempfile as _tf, zipfile as _zf
_zp = os.path.join(_tf.mkdtemp(), "prog.zip")
with _zf.ZipFile(_zp, "w") as z:
    for d in ("300x250", "970x250"):
        z.writestr(f"{d}/index.html", "<html></html>")
        z.writestr(f"{d}/img.png", b"x")


def _srv_cr(dim, **kw):
    return {"name": dim, "type": "html5", "packaged": False,
            "source_path": dim, "status": "new",
            "unit": {"dimension": dim, "source_path": dim, "package": None, "_zip": _zp},
            **kw}


SRV_CAMP = {"id": "C9", "name": "prog", "status": "existing",
            "startDate": "2026-08-01", "endDate": "2026-12-31",
            "defaultLandingPageId": "OLD_DEFAULT"}
SRV_PLACEMENT = {
    "name": "prog_kv1_11.08.2026-prospecting", "serving": True, "site": "CG_Programmatic",
    "sizes": ["300x250", "970x250"], "size": "300x250",
    "ads": [{"name": "Display", "creatives": [
        _srv_cr("300x250", lpName="linia1-programmatic-prospecting",
                lpUrl="https://x?a=p"),
        _srv_cr("970x250", lpName="linia1-programmatic-prospecting",
                lpUrl="https://x?a=p")]}]}
SRV_FULL = {"site": {"name": "CG_Programmatic"},
            "line": {"lpName": "linia1-programmatic-default", "url": "https://x?a=d"},
            "placements": [SRV_PLACEMENT]}
srv_state = {"sites_by_name": {"CG_Programmatic": "S1"}, "placements": {}, "ads": {},
             "ad_creatives": {}, "creatives_by_name": {},
             "lps_by_name": {}, "adv_lp_by_name_url": {}}
osrv = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=SRV_CAMP,
                    dry_run=True)
lsrv = osrv.run(SRV_FULL, srv_state)
acts = [(e["action"], e["kind"], e["name"]) for e in lsrv]
check("każdy wymiar dostaje wgrany materiał",
      [e["name"] for e in lsrv if e["kind"] == "asset"], ["300x250.zip", "970x250.zip"])
check("...i własną kreację serwowaną",
      [(e["action"], e["name"]) for e in lsrv if e["kind"] == "creative"],
      [("CREATE", "300x250"), ("CREATE", "970x250")])
check("kreacja serwująca to DISPLAY z assetem, nie szablon 1x1",
      all("DISPLAY + asset" in e["detail"] for e in lsrv if e["kind"] == "creative"), True)
check("placement deklaruje WSZYSTKIE wymiary",
      next(e["detail"] for e in lsrv if e["kind"] == "placement"),
      "site=CG_Programmatic, wymiary: 2")
check("JEDEN ad standardowy ze wszystkimi kreacjami",
      [(e["action"], e["name"], e["detail"]) for e in lsrv if e["kind"] == "ad"],
      [("CREATE", "Display",
        "AD_SERVING_STANDARD_AD, 2 kreacji -> LP linia1-programmatic-prospecting")])
# LP `-default` musi zostać defaultem kampanii — z niego CM bierze adres dla adów
# `{wymiar} Default Web Ad`, których w naszym drzewie nie ma
check("LP -default zostaje domyślną stroną kampanii, mimo że kampania już miała inną",
      next(e["detail"] for e in lsrv if e["kind"] == "campaign-LP"),
      "jako default kampanii (programmatic)")
check("kolejność: asset przed kreacją, kreacja przed adem",
      [i for i, a in enumerate(acts) if a[1] in ("asset", "creative", "ad")] ==
      sorted(i for i, a in enumerate(acts) if a[1] in ("asset", "creative", "ad")), True)
# brak materiału nie może po cichu dać pustej kreacji
NOMAT = {**SRV_FULL, "placements": [{**SRV_PLACEMENT, "ads": [{"name": "Display",
         "creatives": [_srv_cr("111x111", **{"unit": {"dimension": "111x111",
                       "source_path": "nie-ma", "package": None, "_zip": _zp}})]}]}]}
lno = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=SRV_CAMP,
                   dry_run=True).run(NOMAT, dict(srv_state))
check("brak materiału -> SKIP z powodem, nie pusta kreacja",
      [(e["action"], e["kind"]) for e in lno if e["action"] == "SKIP"],
      [("SKIP", "creative"), ("SKIP", "ad")])

print("\nPLACEMENT SERWUJĄCY bez writera — musi być odrzucony PRZED zapisem:")
# `run()` czyta tylko name/ads/creatives, więc taki placement zapisałby się jako zwykły
# tracking 1x1 i wyszłoby to dopiero w CM360. Do usunięcia razem z writerem (Etap 2).
SRV_PROP = {"placements": [
    {"name": "kampania_kv1_11.08.2026-prospecting", "serving": True,
     "sizes": ["300x250", "970x250"], "ads": [{"name": "Display", "creatives": []}]},
    {"name": "Display", "ads": [{"name": "300x250", "creatives": []}]}]}
check("wykrywa placement serwujący po fladze, nie po nazwie",
      Orchestrator.serving_names(SRV_PROP),
      ["kampania_kv1_11.08.2026-prospecting"])
check("zwykła propozycja trackingowa przechodzi",
      Orchestrator.serving_names({"placements": [
          {"name": "Display", "ads": []}]}), [])
check("pusta propozycja nie wysypuje sprawdzenia",
      Orchestrator.serving_names({}), [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
