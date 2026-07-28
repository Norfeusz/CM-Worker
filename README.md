# CM Worker

Narzędzie-interfejs dla Campaign Manager 360. Z **linku (LP) + zipa z materiałami + wiadomości zlecenia**
buduje w CM strukturę **Site → Placement → Ad → Creative** (+ landing page linii) i generuje tagi.
Logika jest **deterministyczna**, a AI wchodzi tylko w punktach eskalacji (fallback).

> CM jest tu używany do **trackingu/tagów** — kreacje to proste szablony 1×1 (`TRACKING_TEXT`), bez uploadu assetów.
> Realne materiały żyją na GDN/FB. Tag = jedna trójka (Placement × Ad × Creative).

## Wymagania
- Windows, **Python 3.14** przez launcher `py` (Node nie jest potrzebny — UI to prototyp bez builda).
- Instalacja zależności:
```bash
py -m pip install -r requirements.txt
```
- Autoryzacja CM360 (raz; otwiera przeglądarkę, zapisuje `credentials/token.json`):
```bash
py scripts/cm_auth.py
```

## Bezpiecznik (ważne)
`scripts/cm_auth.py` wymusza w kodzie:
- **tylko profil 9556074 (Cube Group, test)** i **advertiser 11992166** — reszta (w tym MBank) twardo zablokowana,
- **GET zawsze**, **POST/PUT tylko** przez `service(read_only=False)`, **DELETE nigdy**.
Zdejmowane dopiero na produkcję (rozszerzenie allowlisty).

## Jak testować (obecny format)

### 1. UI z formularzem (główny sposób) — `serve.py`
Uruchom backend (serwuje UI **i** buduje propozycje; bez npm) i otwórz:
```bash
py scripts/serve.py
```
```bash
start http://127.0.0.1:8765/
```
W UI, w karcie **„📝 Nowe zlecenie"**: wklej **Adres LP**, wybierz **Źródło**, wgraj **.zip** z materiałami,
(opcjonalnie) wklej **wiadomość zlecenia** → **„Analizuj → zbuduj propozycję"**. Dostajesz drzewo
Site→Placement→Ad→Creative z pytaniami sterującymi, panelem linii i licznikiem tagów.
Pod tagami jest **okienko uwag** + „✨ Popraw strukturę wg uwag (AI)" — uwagi trafiają do Agenta AI
(podpięcie w n8n; na razie zwraca komunikat). Działa też drag&drop Adów, edycja nazw, „Wczytaj JSON",
ręczne dodawanie placementów/adów/creative (z listy istniejących lub nowe), własny LP per creative,
oraz „🔁 zastosuj do wszystkich" na każdym creative — dodaje go (lub przemianowuje odpowiednik) na
wszystkich pozostałych adach w strukturze, zamiast klikać to samo ręcznie na każdym z osobna.
Gdy żadna kampania nie pasuje do linku, pojawia się **przeglądarka kampanii** advertisera — kliknij
nazwę, aby rozwinąć jej strony docelowe, albo „Użyj tej kampanii", aby zbudować propozycję wprost na niej.

### 2. Pełny pipeline z CLI (alternatywa)
Podgląd planu bez zapisu (dry-run), albo realny zapis + tagi na koncie testowym:
```bash
py scripts/demo.py --link "<url>" --zip "<plik.zip>" --source GDN
```
```bash
py scripts/demo.py --link "<url>" --zip "<plik.zip>" --source GDN --execute --export
```

### 3. Podgląd konta testowego (read-only)
```bash
py scripts/cm_tree.py tree 9556074 36430023
```
(`advertisers` / `campaigns <profileId> [advertiserId]` / `lps <profileId> <campaignId>` / `tree`)

### 4. Pojedyncze klocki
```bash
py scripts/match_link.py --test "<url>"
```
```bash
py parser/parse_zip.py "<plik.zip>"
```
```bash
py scripts/export_tags.py <campaignId> <creativeId> <adId>[,<adId>...]
```

## Testy
```bash
py tests/test_matcher.py ; py tests/test_proposal.py ; py tests/test_orchestrate.py
```

## Struktura repo
```
scripts/
  cm_auth.py        # OAuth + bezpiecznik (allowlist, GET-only/writes-scoped)
  cm_read.py        # odczyt stanu (sites/placements/ads/creatives/LP)
  cm_tree.py        # read-only przeglądarka drzewa
  matcher.py        # link → advertiser/kampania/linia + konflikt linii  (czysty rdzeń)
  match_link.py     # matcher podpięty pod żywe API (--test routuje na konto testowe)
  build_proposal.py # parser+matcher+źródło → kontrakt drzewa (+ questions)
  orchestrate.py    # zapis: LP → REGISTER do kampanii → site → creative → assoc → placement → ad
  cm_write.py       # niskopoziomowe insert/update (dry-run domyślnie)
  export_tags.py    # generatetags → .xls delta (format CG)
  ai_fallback.py    # wykrywanie eskalacji + kontrakt Agenta AI (seam do n8n)
  serve.py          # backend stdlib: serwuje UI + /api/build-proposal + /api/refine
  demo.py           # spięcie całości end-to-end (CLI)
parser/parse_zip.py # analiza zipa (wymiary, warianty, typy, wiele źródeł/grup)
config/             # advertiser_map.json (URL→advertiser), source_map.json (source→Site/Placement)
ui/index.html       # prototyp React (bez builda), serwowany http.server
data/samples/       # przykładowe zipy (zbiór ewaluacyjny)
docs/n8n-ai-architecture.md  # projekt wpięcia n8n + Agenta
tests/              # testy offline rdzenia
```

## Kluczowe reguły domenowe
- **advertiser** = człony URL (np. `.../indywidualny/ubezpieczenia/...`), z pominięciem prefiksu `/lp2/2026/c1/`.
- **kampania** = reszta ścieżki po członach advertisera (bez `utm`); brak wspólnego członu → sugestia nowej kampanii.
- **linia** = numer wg ścieżki docelowej; źródło (GDN/FB) = sufiks (`liniaN-GDN`); ta sama ścieżka + inne źródło → ta sama linia.
  Ta sama ścieżka + to samo źródło, inny query (`sprzedawca`) → **pytanie** (reuse vs nowa linia).
- **Ad** = wymiar (GDN) / wariant (DemGen) / `KV_wym_karta` (FB karuzela); jeden Ad trzyma wiele creative (linii).
- **LP linii** trzeba **dodać do listy stron docelowych kampanii** zanim zadziała (robione trikiem `defaultLandingPageId`-cycle).
- **Nowa kampania**: data końca = start + 5 lat; `euPoliticalAdsDeclaration` = brak treści politycznych.

## Status / dalej
- ✅ Pipeline end-to-end na żywym koncie testowym: match → parser → propozycja(+pytania) → zapis → tagi `.xls`.
- ⬜ Serwis FastAPI eksponujący moduły + flow n8n + Node AI Agent (patrz `docs/n8n-ai-architecture.md`).
- ⬜ Docelowy build React (wymaga nowszego Node — obecnie 14).
- ⬜ Trening parsera/Agenta na parach `(zip + docelowa struktura)`.
