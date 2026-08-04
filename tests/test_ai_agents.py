"""Offline tests for the AI agent layer: schema validation + deterministic op application.

No network, no model. What is verified here is the part that must hold even when the model
answers badly: a reply that breaks the contract is rejected, and an op addressing something
that no longer exists is skipped rather than silently doing nothing to the wrong node.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import build_proposal as B
import ai_agents as A

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "data", "samples")
passed = failed = 0


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


def op(kind, **kw):
    """An op with every schema key present, like the model is required to send."""
    full = {"op": kind, "placement": None, "ad": None, "creative": None, "name": None,
            "to": None, "lpName": None, "lpUrl": None, "reason": "test"}
    full.update(kw)
    return full


print("walidacja schematu odpowiedzi (to, co chroni nas przed złą odpowiedzią modelu):")
good = {"ops": [op("rename_ad", placement="Display", ad="300x250", to="300x600")],
        "confidence": 0.9, "notes": "", "unclear": []}
check("poprawna odpowiedź przechodzi", A.validate(good, A.INTENT_SCHEMA), [])

missing = {"ops": [], "confidence": 0.5, "notes": ""}
check("brak wymaganego pola -> błąd",
      A.validate(missing, A.INTENT_SCHEMA), ["$.unclear: brak wymaganego pola"])

extra = dict(good, sneaky="x")
check("nieoczekiwane pole -> błąd",
      A.validate(extra, A.INTENT_SCHEMA), ["$.sneaky: nieoczekiwane pole"])

badtype = dict(good, confidence="wysoka")
check("zły typ -> błąd",
      A.validate(badtype, A.INTENT_SCHEMA), ["$.confidence: oczekiwano number, jest string"])

badop = {"ops": [op("drop_database")], "confidence": 1, "notes": "", "unclear": []}
check("operacja poza słownikiem -> błąd", len(A.validate(badop, A.INTENT_SCHEMA)), 1)

check("null tam, gdzie schemat pozwala", A.validate(
    {"advertiser_guess": None, "group_mappings": [], "ad_naming": [], "lines": [],
     "resolved_questions": [], "confidence": 0.0, "notes": ""}, A.STRUCTURE_SCHEMA), [])

nested = {"advertiser_guess": None, "group_mappings": [
    {"folder": "Screening", "source": "GDN", "site": "CG_GDN", "placement": "Screening",
     "adKey": "wymyślony", "confidence": 0.8, "reason": "x"}],
    "ad_naming": [], "lines": [], "resolved_questions": [], "confidence": 0.5, "notes": ""}
check("zły enum w zagnieżdżonej tablicy -> wskazana ścieżka",
      A.validate(nested, A.STRUCTURE_SCHEMA)[0].startswith("$.group_mappings[0].adKey"), True)

# ---------------------------------------------------------------------------
parsed = parse_zip.parse(os.path.join(SAMPLES, "GDN Citi.zip"))
camp = {"id": "C1", "name": "demo", "status": "existing",
        "startDate": "2026-06-01", "endDate": "2026-08-31"}
line = {"lineNumber": 2, "lpName": "linia2-GDN", "source": "GDN",
        "path": "nieruchomosci/promocja", "reused": False}
base = B.build_proposal("GDN", parsed, camp, line, target_url="https://x/n/p")
PL = base["placements"][0]["name"]
ADS = [a["name"] for a in base["placements"][0]["ads"]]

print(f"\nstosowanie operacji (placement {PL!r}, {len(ADS)} adów):")
np, log = A.apply_ops(base, [op("rename_ad", placement=PL, ad=ADS[0], to="NOWY")])
check("rename_ad zmienia nazwę", [a["name"] for a in np["placements"][0]["ads"]][0], "NOWY")
check("rename_ad zaraportowany jako ok", log[0]["ok"], True)
check("oryginał nietknięty (kopia, nie mutacja)",
      base["placements"][0]["ads"][0]["name"], ADS[0])

np, log = A.apply_ops(base, [op("rename_ad", placement=PL, ad="nie-ma-takiego", to="X")])
check("rename_ad nieistniejącego -> POMINIĘTE, nie wyjątek", log[0]["ok"], False)
check("nic się nie zmieniło", [a["name"] for a in np["placements"][0]["ads"]], ADS)

print("\nzmiana nazwy creative jest zawężona do konkretnego ada (regresja z historii UI):")
two = A.apply_ops(base, [op("add_creative", placement=PL, ad=ADS[1], name="linia2")])[0]
np, log = A.apply_ops(two, [op("rename_creative", placement=PL, ad=ADS[0],
                              creative="linia2", to="linia2-slonce")])
names = {a["name"]: [c["name"] for c in a["creatives"]] for a in np["placements"][0]["ads"]}
check("przemianowany tylko creative na wskazanym adzie", names[ADS[0]], ["linia2-slonce"])
check("sąsiedni ad NIE ruszony", names[ADS[1]], ["linia2"])

print("\npozostałe operacje:")
np, _ = A.apply_ops(base, [op("add_placement", name="Screening")])
check("add_placement dokłada pusty placement",
      [(pl["name"], len(pl["ads"])) for pl in np["placements"]][-1], ("Screening", 0))

np, log = A.apply_ops(base, [op("add_placement", name=PL)])
check("add_placement duplikatu -> pominięte", log[0]["ok"], False)

np, _ = A.apply_ops(base, [op("add_placement", name="Screening"),
                           op("move_ad", placement=PL, ad=ADS[0], to="Screening")])
check("move_ad przenosi między placementami",
      ([a["name"] for a in np["placements"][0]["ads"]].count(ADS[0]),
       [a["name"] for a in np["placements"][-1]["ads"]]), (0, [ADS[0]]))

np, log = A.apply_ops(base, [op("apply_creative_to_all", name="linia9",
                               lpName="linia9-GDN", lpUrl="https://x/9")])
per_ad = {len(a["creatives"]) for a in np["placements"][0]["ads"]}
check("apply_creative_to_all dokłada wszędzie po jednym", per_ad, {2})
check("i raportuje liczbę adów", log[0]["detail"].endswith(f"na {len(ADS)} adach"), True)
check("własny LP zapisany na dołożonym creative",
      np["placements"][0]["ads"][0]["creatives"][-1]["lpName"], "linia9-GDN")
check("liczba tagów przeliczalna po zmianach (kontrakt nadal spójny)",
      len(B.compute_tags(np)), len(ADS) * 2)

print("\napply_creative_to_all na creative, który JUŻ jest na wszystkich adach — realny błąd\n"
      "z żywego testu: przy wielu LP w jednym zleceniu KAŻDY creative jest na każdym adzie,\n"
      "więc operacja przypisania LP nie robiła nic, a raportowała sukces:")
np, log = A.apply_ops(base, [op("apply_creative_to_all", name="linia2",
                               lpName="linia8-GDN", lpUrl="https://x/8")])
lps = {(c.get("lpName"), c.get("lpUrl"))
       for a in np["placements"][0]["ads"] for c in a["creatives"]}
check("LP ustawione na ISTNIEJĄCYCH creative", lps, {("linia8-GDN", "https://x/8")})
check("zaraportowane jako zastosowane", log[0]["ok"], True)
check("detail mówi o LP, nie o dodawaniu", "LP" in log[0]["detail"], True)
check("nie zduplikował creative",
      {len(a["creatives"]) for a in np["placements"][0]["ads"]}, {1})

np2, log2 = A.apply_ops(np, [op("apply_creative_to_all", name="linia2",
                               lpName="linia8-GDN", lpUrl="https://x/8")])
check("powtórzona operacja bez efektu -> POMINIĘTA, a nie fałszywy sukces",
      log2[0]["ok"], False)

part = A.apply_ops(base, [op("delete_creative", placement=PL, ad=ADS[1],
                             creative="linia2")])[0]
np3, log3 = A.apply_ops(part, [op("apply_creative_to_all", name="linia2",
                                  lpName="linia8-GDN", lpUrl="https://x/8")])
check("mieszane: dokłada gdzie brak I przelinkowuje gdzie już jest",
      ("dodany na 1 adach" in log3[0]["detail"],
       f"ustawione na {len(ADS) - 1} adach" in log3[0]["detail"]), (True, True))

np, log = A.apply_ops(base, [op("delete_creative", placement=PL, ad=ADS[0], creative="linia2")])
check("delete_creative zostawia ada bez creative",
      len(np["placements"][0]["ads"][0]["creatives"]), 0)

np, log = A.apply_ops(base, [op("set_creative_lp", placement=PL, ad=ADS[0],
                               creative="linia2", lpName="linia2-niebo",
                               lpUrl="https://x/niebo")])
cr = np["placements"][0]["ads"][0]["creatives"][0]
check("set_creative_lp ustawia własny LP", (cr["lpName"], cr["lpUrl"]),
      ("linia2-niebo", "https://x/niebo"))

np, log = A.apply_ops(base, [op("rename_ad", placement=PL, ad=ADS[0])])
check("brak `to` przy zmianie nazwy -> pominięte", log[0]["ok"], False)

np, log = A.apply_ops(base, [op("nonsense")])
check("nieznana operacja -> pominięta z powodem",
      (log[0]["ok"], "nieznana" in log[0]["detail"]), (False, True))

print("\ntolerancja na wartość w innym polu (realny błąd z żywego testu Gemini —\n"
      "model rozumiał zlecenie, ale wstawiał nazwy w inne pola, niż oczekiwał apply_ops):")
np, log = A.apply_ops(base, [op("add_placement", placement="Screening")])
check("add_placement z nazwą w `placement` -> zastosowane", log[0]["ok"], True)
check("i placement faktycznie powstał",
      [pl["name"] for pl in np["placements"]][-1], "Screening")

np, log = A.apply_ops(base, [op("add_placement", to="Screening")])
check("add_placement z nazwą w `to` -> zastosowane", log[0]["ok"], True)

np, log = A.apply_ops(base, [op("add_ad", placement=PL, ad="999x999")])
check("add_ad z nazwą w `ad` -> zastosowane", log[0]["ok"], True)
check("i ad faktycznie powstał",
      [a["name"] for a in np["placements"][0]["ads"]][-1], "999x999")

np, log = A.apply_ops(base, [op("add_placement", placement="Screening"),
                             op("move_ad", placement=PL, ad=ADS[0], name="Screening")])
check("move_ad z celem w `name` -> zastosowane", log[1]["ok"], True)
check("ad rzeczywiście przeniesiony",
      [a["name"] for a in np["placements"][-1]["ads"]], [ADS[0]])

np, log = A.apply_ops(base, [op("add_placement")])
check("nadal odrzuca add_placement bez ŻADNEJ nazwy", log[0]["ok"], False)

print("\nZMIANA NAZWY LINII W CAŁYM DRZEWIE — realny błąd z sesji użytkownika: prośba\n"
      "„dopisz coś do nazwy linii” to RENAME, ale brakowało takiej operacji, więc model\n"
      "użył apply_creative_to_all (która DOKŁADA) → duplikaty, a po usunięciu starych\n"
      "wszystkie linie wylądowały na wszystkich wymiarach:")
# drzewo z przypisaniem per folder: linia9 tylko na CZĘŚCI adów (jej folder ma mniej wymiarów)
uneven = A.apply_ops(
    B.build_proposal("GDN", parsed, camp, lines=[
        {"lineNumber": 8, "lpName": "linia8-GDN", "creativeName": "linia8",
         "source": "GDN", "path": "a", "reused": False, "url": "https://x/frc"},
        {"lineNumber": 9, "lpName": "linia9-GDN", "creativeName": "linia9",
         "source": "GDN", "path": "b", "reused": False, "url": "https://x/konto"}]),
    [op("delete_creative", placement=PL, ad=ADS[0], creative="linia9"),
     op("delete_creative", placement=PL, ad=ADS[1], creative="linia9")])[0]
before_tags = len(B.compute_tags(uneven))
check("drzewo ma kształt „per folder” (ady mają RÓŻNE zestawy creative)",
      A._per_folder_shape(uneven["placements"]), True)

# 1) właściwa droga: rename_creative_all
np, log = A.apply_ops(uneven, [op("rename_creative_all", creative="linia8",
                                 to="linia8-firmootwieracz")])
names = {c["name"] for pl in np["placements"] for a in pl["ads"] for c in a["creatives"]}
check("rename_creative_all przemianowuje wszędzie", "linia8-firmootwieracz" in names, True)
check("...i nie zostawia starej nazwy", "linia8" in names, False)
check("...i NIE zmienia liczby tagów", len(B.compute_tags(np)), before_tags)
check("...i nie dokłada linii9 tam, gdzie jej nie było",
      [len(a["creatives"]) for a in np["placements"][0]["ads"][:2]], [1, 1])
check("raport mówi wprost, że nic nie dołożono",
      "nic nie dołożono" in log[0]["detail"], True)

# 2) zła droga jest teraz zablokowana
np2, log2 = A.apply_ops(uneven, [op("apply_creative_to_all",
                                    name="linia8-firmootwieracz")])
check("apply_creative_to_all NIE spłaszcza przypisania z folderów",
      len(B.compute_tags(np2)), before_tags)
check("...i jest zaraportowane jako POMINIĘTE", log2[0]["ok"], False)
check("...z podpowiedzią, czego użyć zamiast",
      "rename_creative_all" in log2[0]["detail"], True)

# 3) w drzewie o jednolitym kształcie dokładanie nadal działa (to jego prawdziwy cel)
even = B.build_proposal("GDN", parsed, camp, line=line)
np3, log3 = A.apply_ops(even, [op("apply_creative_to_all", name="linia-nowa")])
check("jednolite drzewo -> dokładanie nadal dozwolone",
      (log3[0]["ok"], len(B.compute_tags(np3))),
      (True, len(B.compute_tags(even)) + len(ADS)))

check("rename_creative_all bez nowej nazwy -> pominięte",
      A.apply_ops(uneven, [op("rename_creative_all", creative="linia8")])[1][0]["ok"], False)
check("rename_creative_all nieistniejącej linii -> pominięte",
      A.apply_ops(uneven, [op("rename_creative_all", creative="nie-ma", to="x")])[1][0]["ok"],
      False)
check("rename_creative_all może od razu ustawić LP",
      {c.get("lpName") for pl in A.apply_ops(uneven, [op(
          "rename_creative_all", creative="linia8", to="linia8-frc",
          lpName="linia8-frc-GDN", lpUrl="https://x/frc")])[0]["placements"]
       for a in pl["ads"] for c in a["creatives"] if c["name"] == "linia8-frc"},
      {"linia8-frc-GDN"})

print("\nsugestie roli (a) -> operacje roli (b) (jedna sprawdzona ścieżka stosowania):")
multi = B.build_proposal("GDN", parsed, camp, lines=[
    {"lineNumber": 8, "lpName": "linia8-GDN", "creativeName": "linia8", "source": "GDN",
     "path": "a", "reused": False, "url": "https://x/frc"},
    {"lineNumber": 9, "lpName": "linia9-GDN", "creativeName": "linia9", "source": "GDN",
     "path": "b", "reused": False, "url": "https://x/konto"}])
sugg = {"advertiser_guess": None, "group_mappings": [
            {"folder": "FRC GIF", "source": "GDN", "site": "CG_GDN", "placement": "GIF",
             "adKey": "dimension", "confidence": 0.9, "reason": "x"}],
        "ad_naming": [{"unit": "FRC GIF_160x600", "adName": "160x600_gif", "reason": "x"}],
        "lines": [
            {"lpUrl": "https://x/frc", "source": "GDN", "audience": "firmootwieracz",
             "lpName": "linia8-firmootwieracz-GDN", "creativeName": "linia8-firmootwieracz"},
            {"lpUrl": "https://x/nie-ma-takiego", "source": "GDN", "audience": None,
             "lpName": "linia99-GDN", "creativeName": "linia99"}],
        "resolved_questions": [], "confidence": 0.8, "notes": ""}
np, log, notes = A.apply_suggestions(multi, sugg)
names = {c["name"] for pl in np["placements"] for a in pl["ads"] for c in a["creatives"]}
check("creative przemianowany wszędzie, gdzie był",
      ("linia8-firmootwieracz" in names, "linia8" in names), (True, False))
check("druga linia nietknięta (sugestia jej nie dotyczyła)", "linia9" in names, True)
lp8 = {c.get("lpName") for pl in np["placements"] for a in pl["ads"]
       for c in a["creatives"] if c["name"] == "linia8-firmootwieracz"}
check("LP linii ustawione na przemianowanym creative", lp8, {"linia8-firmootwieracz-GDN"})
check("metadane linii nadążyły za drzewem",
      (np["lines"][0]["creativeName"], np["lines"][0]["lpName"]),
      ("linia8-firmootwieracz", "linia8-firmootwieracz-GDN"))
check("LP z obcym URL-em pominięte (dopasowanie po URL, nigdy po pozycji)",
      any("nie-ma-takiego" in n for n in notes), True)
check("nazwy adów pominięte z wyjaśnieniem (rozbicie na formaty robi rdzeń)",
      any("fileFormats" in n for n in notes), True)
check("mapowania folderów pominięte z wyjaśnieniem (to materiał do configu)",
      any("promote.py" in n for n in notes), True)
check("wszystkie przetłumaczone operacje faktycznie się zastosowały",
      [e["ok"] for e in log], [True] * len(log))
# przemianowanie linii i wskazanie jej LP NIE MOŻE rozdmuchać struktury: pierwsza wersja
# używała apply_creative_to_all i dokładała linii wymiary, których jej folder nie miał
check("liczba tagów bez zmian (żaden creative nie został DOŁOŻONY)",
      len(B.compute_tags(np)), len(B.compute_tags(multi)))
check("żadna operacja nie jest typu dokładającego creative",
      {e["op"] for e in log} <= {"rename_creative", "set_creative_lp"}, True)

# linia obecna tylko na CZĘŚCI adów (tak wygląda paczka, w której folder jednej linii ma
# mniej wymiarów) — sugestia ma ruszyć tylko te ady, a nie dorobić brakujące
thin = A.apply_ops(multi, [op("delete_creative", placement=PL, ad=ADS[0],
                              creative="linia9")])[0]
before = len(B.compute_tags(thin))
np_thin, log_thin, _ = A.apply_suggestions(thin, {"lines": [
    {"lpUrl": "https://x/konto", "source": "GDN", "audience": "konto",
     "lpName": "linia9-konto-GDN", "creativeName": "linia9-konto"}]})
check("linia o mniejszym pokryciu nie dostaje brakujących adów",
      len(B.compute_tags(np_thin)), before)
check("ad, z którego linię usunięto, jej NIE odzyskał",
      any(c["name"].startswith("linia9")
          for c in np_thin["placements"][0]["ads"][0]["creatives"]), False)

check("brak sugestii linii -> zero operacji, ale wyjaśnienia zostają",
      A.suggestions_to_ops(multi, {"ad_naming": [{"unit": "x", "adName": "y", "reason": ""}]})[0],
      [])

print("\nprompt roli (b) musi podawać znaczenie pól per operacja "
      "(bez tego model zgaduje i operacje są pomijane):")
for o in A.OPS:
    check(f"tabela pól opisuje {o}", o in A.INTENT_SYSTEM, True)
check("wyjaśnione, że `to` trzyma NOWĄ wartość",
      "`to` always holds the NEW value" in A.INTENT_SYSTEM, True)

print("\nzawartość zipa MUSI iść do roli (b) — bez niej agent słusznie odmawia\n"
      "(realny przypadek: „wymiary zgodnie z zawartością paczki” -> zero operacji):")
ZIPVIEW = {"source_hint": "GDN", "format_hint": "Display",
           "groups": [], "dimensions": ["160x600", "250x250"],
           "units": [{"dimension": "160x600", "variant": "GIF", "card_index": None,
                      "type": "gif", "group": None},
                     {"dimension": "160x600", "variant": "HTML", "card_index": None,
                      "type": "html5", "group": None}]}
with_ai = dict(base, ai={"request": {"zip": ZIPVIEW}})
req_zip = A.build_intent_request(with_ai, "Wymiary dla GIF i HTML zgodnie z paczką")
check("zip dołączony do żądania", req_zip["zip"], ZIPVIEW)
check("warianty z folderów widoczne dla agenta",
      sorted({u["variant"] for u in req_zip["zip"]["units"]}), ["GIF", "HTML"])
check("bez sekcji ai propozycja nadal działa (zip = None)",
      A.build_intent_request(base, "x")["zip"], None)
check("prompt każe korzystać z zipa",
      "they are in `zip`" in A.INTENT_SYSTEM, True)
check("prompt ostrzega, że nowy placement jest PUSTY",
      "newly created placement is EMPTY" in A.INTENT_SYSTEM, True)

print("\nkontrakt żądania dla roli (b):")
req = A.build_intent_request(base, "Screening to osobny placement")
check("żądanie ma uwagi, strukturę, zip i słownik operacji",
      sorted(req), ["allowed_ops", "answers", "instructions", "remarks", "structure", "zip"])
check("struktura zawiera tylko nazwy (bez id/statusów)",
      sorted(req["structure"]["placements"][0]), ["ads", "name"])
check("słownik operacji zgodny ze schematem",
      req["allowed_ops"], A.INTENT_SCHEMA["properties"]["ops"]["items"]["properties"]["op"]["enum"])

print("\ntransport do n8n (udawany webhook na localhoście — bez sieci i bez klucza):")
import json as _json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

REPLY = {"mode": "ok"}
SEEN = {}


class _Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        SEEN["payload"] = _json.loads(self.rfile.read(n) or b"{}")
        SEEN["token"] = self.headers.get("X-CM-Token")
        mode = REPLY["mode"]
        if mode == "http500":
            self.send_response(500); self.end_headers(); self.wfile.write(b"boom"); return
        if mode == "notjson":
            body = b"<html>nie json</html>"
        elif mode == "badschema":
            body = _json.dumps({"ops": [], "confidence": "duza"}).encode()
        elif mode == "wrapped":       # n8n often wraps a single item like this
            body = _json.dumps([{"output": {
                "ops": [op("rename_ad", placement="Display", ad="300x250", to="300x600")],
                "confidence": 0.8, "notes": "ok", "unclear": []}}]).encode()
        else:
            body = _json.dumps({"ops": [], "confidence": 0.1, "notes": "", "unclear": ["co?"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["N8N_INTENT_URL"] = f"http://127.0.0.1:{srv.server_port}/webhook/intent"
os.environ["N8N_TOKEN"] = "tajne123"

check("configured() widzi ustawiony URL", A.configured("N8N_INTENT_URL"), True)

REPLY["mode"] = "ok"
res = A.intent_call()(A.build_intent_request(base, "nic"))
check("poprawna odpowiedź przechodzi przez transport", res["unclear"], ["co?"])
check("prompt i schemat lecą w payloadzie (n8n zostaje przekaźnikiem)",
      sorted(SEEN["payload"]), ["input", "schema", "system"])
check("payload niesie nasz prompt systemowy",
      SEEN["payload"]["system"].startswith("You map advertising creative deliveries"), True)
check("token współdzielony wysłany w nagłówku", SEEN["token"], "tajne123")

REPLY["mode"] = "wrapped"
res = A.intent_call()(A.build_intent_request(base, "nic"))
check("owijka [{output:...}] z n8n rozpakowana", res["ops"][0]["to"], "300x600")

for mode, fragment in (("badschema", "nie pasuje do schematu"),
                       ("notjson", "nie-JSON"),
                       ("http500", "odpowiedziało 500")):
    REPLY["mode"] = mode
    try:
        A.intent_call()(A.build_intent_request(base, "nic"))
        check(f"{mode} -> AgentError", False, True)
    except A.AgentError as e:
        check(f"{mode} -> AgentError z czytelnym powodem", fragment in str(e), True)

del os.environ["N8N_INTENT_URL"]
check("bez zmiennej środowiskowej configured() = False", A.configured("N8N_INTENT_URL"), False)
try:
    A.intent_call()({})
    check("brak URL -> AgentError", False, True)
except A.AgentError as e:
    check("brak URL -> AgentError mówi, co ustawić", "N8N_INTENT_URL" in str(e), True)
srv.shutdown()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
