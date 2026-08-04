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
     "status": "new", "lpName": "linia3-slonce-GDN", "lpUrl": "https://x/slonce?utm_content=slonce"},
    {"name": "linia3-niebo", "type": "html5", "packaged": False, "source_path": None,
     "status": "new", "lpName": "linia3-niebo-GDN", "lpUrl": "https://x/niebo?utm_content=niebo"},
    {"name": "linia3", "type": "html5", "packaged": False, "source_path": None, "status": "new"},
]
state3 = dict(state, creatives_by_name={}, ad_creatives={})
camp_new = dict(camp)  # no defaultLandingPageId -> first LP processed becomes "as default"

orch3 = Orchestrator(svc=None, profile_id="P", advertiser_id="A", campaign=camp_new, dry_run=True)
log3 = orch3.run(proposal3, state3)

lp_creates = {e["name"] for e in log3 if e["kind"] == "landingPage"}
check("3 distinct LPs resolved (2 custom + 1 shared line LP)", lp_creates,
      {"linia3-slonce-GDN", "linia3-niebo-GDN", "linia2-GDN"})
ad_entries = [e for e in log3 if e["kind"] == "ad" and e["name"] == ad3["name"]]
check("ad has 3 log entries (CREATE + 2 append UPDATEs)", len(ad_entries), 3)
check("slonce creative appended with its OWN LP",
      any("linia3-slonce" in e["detail"] and "linia3-slonce-GDN" in e["detail"] for e in ad_entries), True)
check("niebo creative appended with its OWN LP",
      any("linia3-niebo" in e["detail"] and "linia3-niebo-GDN" in e["detail"] for e in ad_entries), True)
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
     "status": "new", "lpName": "linia2-niebo-GDN", "lpUrl": "https://x/niebo"})
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
      [e["name"] for e in log4 if e["kind"] == "campaign-LP"], ["linia2-niebo-GDN"])
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
      sorted(lp5), ["linia1-prospecting-GDN", "linia1-remarketing-GDN"])
check("drugie LP zarejestrowane na liście stron docelowych kampanii",
      [e["name"] for e in log5 if e["kind"] == "campaign-LP"],
      ["linia1-prospecting-GDN", "linia1-remarketing-GDN"])
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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
