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
Pod „Szukaj/dodaj site" możesz **dodać brakujący Site do konta**: „użyj" na wpisie z Site Directory
podpina go pod istniejący wpis, a „➕ Dodaj Site do konta" prowadzi przez plan (dry-run) → zapis;
jeśli plan wykaże, że trzeba utworzyć **nowy wpis w katalogu**, zapis jest zablokowany do
zaznaczenia zgody (wpisy katalogu są ogólnokontowe i nieusuwalne).
Pod tagami jest **okienko uwag** + „✨ Popraw strukturę wg uwag (AI)" — uwagi trafiają do Agenta AI
(podpięcie w n8n; na razie zwraca komunikat). Działa też drag&drop Adów, edycja nazw, „Wczytaj JSON",
ręczne dodawanie placementów/adów/creative (z listy istniejących lub nowe), własny LP per creative,
oraz „🔁 zastosuj do wszystkich" na każdym creative — dodaje go (lub przemianowuje odpowiednik) na
wszystkich pozostałych adach w strukturze, zamiast klikać to samo ręcznie na każdym z osobna.
**Wybór kampanii.** Gdy ścieżka linku pasuje do istniejącej kampanii, narzędzie od razu buduje na niej
strukturę — ale przy nazwie kampanii jest **„🔄 zmień kampanię"**: wybór innej (albo utworzenie nowej)
**przebudowuje propozycję od zera** dla tej kampanii, z tego samego linku i zipa (numer linii, statusy
`nowe`/`istnieje` i licznik tagów są przeliczane; ręczne zmiany w drzewie przepadają).
Gdy nic nie pasuje, ten sam wybór pojawia się od razu po analizie. W obu miejscach jest **przeglądarka
kampanii** advertisera (klik w nazwę rozwija jej strony docelowe) oraz pole nazwy + **„➕ Nowa kampania"**
(prefill = reszta ścieżki URL) — kampania powstaje dopiero przy „Wykonaj w CM360".

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

## Agenci AI (opcjonalnie, przez n8n)

Dwie role: **(a)** podpowiada strukturę w punktach niskiej pewności, **(b)** interpretuje uwagi
i przerabia strukturę. Model żyje w n8n (klucz API nie ląduje na laptopie), prompty i schematy
w repo. Instalacja i workflow do importu: [n8n/README.md](n8n/README.md). Włączenie:

```bash
set N8N_STRUCTURE_URL=https://n8n.firma/webhook/cm-worker-structure
set N8N_INTENT_URL=https://n8n.firma/webhook/cm-worker-intent
set N8N_TOKEN=<wspólny sekret z węzłem „Zbuduj żądanie”>
```

Bez tych zmiennych narzędzie działa jak dotąd — przyciski AI pokazują „nie podpięte”.
Rola (a) zwraca **tylko sugestie**; rola (b) zwraca operacje edycyjne, które Python stosuje
deterministycznie i pokazuje jako diff (z listą pominiętych i pytaniami agenta).

## Testy
```bash
py tests/test_matcher.py ; py tests/test_proposal.py ; py tests/test_orchestrate.py ; py tests/test_create_site.py ; py tests/test_ai_agents.py
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
- **Nowy Site**: `sites.insert` na **wskazanym** wpisie Site Directory to zwykły zapis; utworzenie
  nowego wpisu w katalogu (`directorySites.insert`) wymaga jawnej zgody — wpisy są ogólnokontowe
  i nieusuwalne, a na tym koncie Site często wisi na wpisie o innej nazwie (`CG_GDN` →
  `CG_remarketing`). `/api/commit` odmawia realnego zapisu, jeśli Site jeszcze nie istnieje.
- **Nowa kampania**: start = dziś, data końca = start + 5 lat; `euPoliticalAdsDeclaration` = brak treści
  politycznych. CM wymaga `defaultLandingPageId`, więc **LP linii powstaje przed kampanią** — i tym samym
  jest od razu na jej liście stron docelowych (bez triku default-cycle).

## Status / dalej
- ✅ Pipeline end-to-end na żywym koncie testowym: match → parser → propozycja(+pytania) → zapis → tagi `.xls`.
- ⬜ Serwis FastAPI eksponujący moduły + flow n8n + Node AI Agent (patrz `docs/n8n-ai-architecture.md`).
- ⬜ Docelowy build React (wymaga nowszego Node — obecnie 14).
- ⬜ Trening parsera/Agenta na parach `(zip + docelowa struktura)`.
