"""Tests for the proposal builder: standard GDN case + existing-structure merge."""
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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
