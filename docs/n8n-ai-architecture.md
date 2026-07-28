# CM Worker — architektura n8n + Agent AI

## Zasada: deterministycznie najpierw, AI jako fallback
Reguły (matcher, parser, konwencje source→Site/Placement, numeracja linii) rozwiązują
większość przypadków **bez AI**. Agent AI wchodzi **tylko w punktach eskalacji**, gdy
pewność jest niska — a jego wynik to **podpowiedzi** (człowiek zawsze weryfikuje w UI).

## Komponenty
```
[Frontend React]  --webhook-->  [n8n]  --HTTP-->  [Serwis Python (nasze moduły)]  --API-->  [CM360]
                                  |
                                  +--> [Node: AI Agent]  (tylko dla eskalacji)
```
- **Serwis Python** (cienki wrapper na już zbudowanych modułach; np. FastAPI):
  - `POST /match`      → `matcher` + `match_link` (link → advertiser/kampania/linia, konflikt)
  - `POST /parse`      → `parse_zip` (struktura + grupy źródeł)
  - `POST /proposal`   → `build_proposal` (kontrakt drzewa + `questions`)
  - `POST /escalations`→ `ai_fallback.escalations` + `build_request`
  - `POST /write`      → `orchestrate` (dry-run/real; **bezpiecznik z `cm_auth` zostaje po stronie serwera**)
  - `POST /tags`       → `export_tags` (delta `.xls`)
  - Bezpieczeństwo (allowlist profil/advertiser, GET-only do momentu zapisu) **zostaje w Pythonie** — n8n go nie omija.
- **n8n**: orkiestracja przepływu + webhooki dla frontu + **Node „AI Agent"** wywoływany warunkowo.
- **Frontend**: renderuje kontrakt (`proposal`), pokazuje `questions`, drag&drop/edycja, „Zatwierdź".

## Przepływ (happy path + eskalacje)
1. Front wysyła `{link, zip, źródło, wiadomość}` na webhook n8n.
2. n8n → `/parse` i `/match`.
3. n8n → `/escalations`. Jeśli **pusta** → od razu `/proposal` (bez AI).
4. Jeśli są eskalacje → **Node AI Agent** z payloadem z `ai_fallback.build_request`
   (schemat wyjścia = `ai_fallback.OUTPUT_SCHEMA`). Wynik AI scala się jako podpowiedzi do `/proposal`.
5. Front pokazuje propozycję + `questions` → użytkownik weryfikuje/edytuje/odpowiada.
6. „Zatwierdź" → n8n → `/write` (dry-run → potwierdzenie → real) → `/tags`.

## Punkty eskalacji (kiedy schematy nie wystarczają) — z `ai_fallback.escalations`
- `advertiser`     — żadna reguła URL nie pasuje → AI zgaduje advertisera z listy.
- `group_mapping`  — foldery o nieznanym źródle (np. `Screening`) → AI proponuje source/placement.
- `ad_naming`      — złożony format (Karuzela/Video, karty) → AI proponuje nazwy Adów.
- `lines_audience` — wiadomość z wieloma LP / audience (prospecting/remarketing/…) → AI wyciąga linie.
- `structure`      — wpisy bez wykrywalnego wymiaru → AI interpretuje.

## Agent AI — kontrakt
- **Wejście**: `ai_fallback.build_request(parsed, proposal, message, advertiser_list)`.
- **Wyjście** (walidowane): `advertiser_guess, group_mappings, ad_naming, lines, resolved_questions, confidence, notes`.
- **System prompt** (skrót): model CM360 (Advertiser→Campaign→Site→Placement→Ad→Creative; tag = Ad×Creative;
  linia = LP+audience; Ad=wymiar/wariant wg source; creative 1×1 TRACKING_TEXT), konwencje per-Site
  (GDN→Display; FB→Link/Animacje/Karuzela/Posty; DemGen→Display+Karuzela), reguła numeracji linii,
  polecenie „wypełniaj tylko pewne pola, resztę zostaw człowiekowi".

## Trening / doskonalenie
Pary `(zip + wiadomość → docelowa struktura)` od użytkownika = zbiór ewaluacyjny. Istniejące kampanie
na koncie = źródło konwencji per-Site/advertiser (czytane read-only). Metryka: zgodność propozycji
z ręczną strukturą; słabe przypadki → doprecyzowanie reguł lub promptu Agenta.
