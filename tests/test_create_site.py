"""Offline test of Site creation branches (fake service — no API, no network).

Guards the rule that matters: a brand-new Site Directory entry (account-wide and
non-deletable) is never minted implicitly, while linking a Site to an entry the user
picked — how CG_GDN -> CG_remarketing works on this account — stays a one-call path.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cm_write as W

passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


class _Req:
    def __init__(self, result): self._r = result
    def execute(self): return self._r


class _FakeDirectorySites:
    def __init__(self, svc): self.svc = svc

    def list(self, profileId, searchString, maxResults):
        self.svc.calls.append(("directorySites.list", searchString))
        return _Req({"directorySites": [d for d in self.svc.directory
                                        if searchString.lower() in d["name"].lower()]})

    def insert(self, profileId, body):
        self.svc.calls.append(("directorySites.insert", body["name"], body.get("url")))
        return _Req({"id": "NEW_DS", "name": body["name"]})


class _FakeSites:
    def __init__(self, svc): self.svc = svc

    def insert(self, profileId, body):
        self.svc.calls.append(("sites.insert", body["name"], body["directorySiteId"]))
        return _Req({"id": "NEW_SITE", "name": body["name"]})


class FakeSvc:
    def __init__(self, directory=()):
        self.directory, self.calls = list(directory), []

    def directorySites(self): return _FakeDirectorySites(self)
    def sites(self): return _FakeSites(self)


DIR_WITH_MATCH = [{"id": "DS_MATCH", "name": "CG_Demand_Gen", "url": "https://x"}]
DIR_NO_MATCH = [{"id": "DS_OTHER", "name": "CG_Demand_Gen_stare", "url": "https://x"}]

print("wpis w katalogu o TAKIEJ SAMEJ nazwie -> reuse, bez directorySites.insert:")
svc = FakeSvc(DIR_WITH_MATCH)
r = W.create_site(svc, "P", "CG_Demand_Gen", dry_run=False)
check("tylko list + sites.insert", svc.calls,
      [("directorySites.list", "CG_Demand_Gen"), ("sites.insert", "CG_Demand_Gen", "DS_MATCH")])
check("nie utworzono wpisu katalogu", r["_createdDirectorySite"], False)
check("Site podpięty pod znaleziony wpis", r["_directorySiteId"], "DS_MATCH")

print("\ndopasowanie po nazwie ignoruje wielkość liter:")
svc = FakeSvc([{"id": "DS_MATCH", "name": "cg_demand_GEN"}])
W.create_site(svc, "P", "CG_Demand_Gen", dry_run=False)
check("reuse mimo innej wielkości liter",
      [c for c in svc.calls if c[0] == "sites.insert"], [("sites.insert", "CG_Demand_Gen", "DS_MATCH")])

print("\nwskazany wpis katalogu (przypadek CG_GDN -> CG_remarketing) -> bez szukania:")
svc = FakeSvc(DIR_NO_MATCH)
r = W.create_site(svc, "P", "CG_GDN", directory_site_id="3410897", dry_run=False)
check("żadnego list/insert w katalogu", svc.calls, [("sites.insert", "CG_GDN", "3410897")])
check("Site na wskazanym wpisie", r["_directorySiteId"], "3410897")

print("\nbrak dopasowania BEZ zgody -> realny zapis musi się wywalić, nic nie zapisując:")
svc = FakeSvc(DIR_NO_MATCH)
try:
    W.create_site(svc, "P", "CG_Demand_Gen", dry_run=False)
    check("podniesiony RuntimeError", False, True)
except RuntimeError as e:
    check("podniesiony RuntimeError", True, True)
    check("komunikat wskazuje wyjście z sytuacji",
          "allow_new_directory_site" in str(e) and "directory_site_id" in str(e), True)
check("nic nie zapisano (tylko odczyt katalogu)",
      [c for c in svc.calls if c[0] != "directorySites.list"], [])

print("\nbrak dopasowania w DRY-RUN -> plan, bez wyjątku i bez zapisu:")
svc = FakeSvc(DIR_NO_MATCH)
plan = W.create_site(svc, "P", "CG_Demand_Gen", url="https://x", dry_run=True)
check("plan mówi, że potrzebny nowy wpis", plan["needsNewDirectorySite"], True)
check("dry-run nic nie zapisał", [c for c in svc.calls if c[0] != "directorySites.list"], [])

print("\nbrak dopasowania ZE zgodą -> directorySites.insert, potem sites.insert:")
svc = FakeSvc(DIR_NO_MATCH)
r = W.create_site(svc, "P", "CG_Demand_Gen", url="https://cg.pl/demgen",
                  allow_new_directory_site=True, dry_run=False)
check("kolejność wywołań", svc.calls,
      [("directorySites.list", "CG_Demand_Gen"),
       ("directorySites.insert", "CG_Demand_Gen", "https://cg.pl/demgen"),
       ("sites.insert", "CG_Demand_Gen", "NEW_DS")])
check("zaraportowano utworzenie wpisu katalogu", r["_createdDirectorySite"], True)

print("\nbez połączenia (svc=None) -> dry-run nadal działa, nie wybucha:")
plan = W.create_site(None, "P", "CG_Demand_Gen", dry_run=True)
check("plan bez sprawdzenia katalogu", plan["needsNewDirectorySite"], True)
check("resolve_directory_site zwraca powód",
      W.resolve_directory_site(None, "P", "X")[1], "bez połączenia z API (nie sprawdzono katalogu)")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
