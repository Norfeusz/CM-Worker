# Log przykładów treningowych (zbiór ewaluacyjny)

Pary `(wiadomość/link + zip → docelowa struktura + decyzje)` do dostrajania parsera i Agenta AI.
Metryka: zgodność propozycji narzędzia z docelową strukturą; słabe przypadki → reguła lub prompt.

---

## 1. Young_2026 — GDN + Screening, konflikt `sprzedawca` (niestandardowy)
- **Zlecenie**: szablon CM do kampanii GDN. `LP: .../indywidualny/konta/young-under/google/300/?...&sprzedawca=gdn_young_13_{device}`
- **Zip**: `Materiały standardowe_.zip` — foldery `GDN/` (9 wym.) + `Screening/` (2 html5 + tapety).
- **Decyzje** (pytania):
  - Kodujemy tylko `GDN` (Screening zignorowany, chyba że pada osobne pytanie o źródło; wtedy osobny placement).
  - W kampanii jest już GDN pod te same wymiary; LP różni się tylko `sprzedawca` (`gdn_rmg_young_13` vs `gdn_young_13`) → **PYTANIE**: reuse czy nowa linia → odpowiedź: **nowa linia** (inne kreacje).
- **Wynik**: nowa linia dopięta do istniejących wymiarów (Display). Eksport tagów = **delta** (linia6 × 9 wym.).
- **Wnioski**: wielo-źródłowy zip → pytanie o grupy; Ad trzyma wiele creative; konflikt query → pytanie; tag = delta.

## 2. BC+leasing — Meta statyki, kampania po wspólnej ścieżce
- **Zlecenie**: source **Meta**. Kodowany: `.../firmy/konta/firmowe/meta/leasing/?utm_source=facebook...`
  Znaleziony w kampanii: `.../firmy/konta/firmowe/meta/konto/?utm_source=facebook...`
- **Zip**: `BC + leasing- Meta Grafiki statyczne.7z` — 4 statyczne jpg: `1080x1920, 1200x1200, 960x1200, 1200x628`.
- **Docelowo**: site **CG_Facebook**, placement **Display**, nowa linia **linia12**; tagi dla site FB + placement display po zmianach.
- **Sprawdzone**: matcher daje common=2 (`firmowe/meta`) → ta sama kampania; `resolve_line` → `linia12-Meta` (nowa).
- **Wnioski (naniesione do configu)**:
  - `Meta` = alias `Facebook` (Site `CG_Facebook`).
  - FB statyki (format Display) → placement **`Display`** (nie „Posty").
  - `.7z` obsługiwane przez `py7zr` (rozpakowywacz czysto-pythonowy).
- **TODO parsera**: `parse_zip` czyta na razie `.zip` (zipfile). Dla `.7z` dodać ścieżkę `py7zr` (te 4 pliki to płaskie jpg → wymiar z nazwy pliku).
