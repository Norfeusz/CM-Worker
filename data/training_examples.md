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

## 3. NNW / reformaty KV1+KV3 — paczki per ŹRÓDŁO w zagnieżdżonych zipach (10.08.2026)
Przypadek zgłoszony po sesji z Agentem, w której struktura wyszła **błędnie** — najdroższy
błąd tego projektu do tej pory, bo agent pytał pięć razy i mimo to oddał złe wymiary.

- **Zlecenie**: kodujemy **tylko GDN**. Kampania dopasowała się poprawnie.
- **Zip**: `wetransfer_kv1_nnw_paczki-z-reformatami…zip`, struktura dwupoziomowa:
  ```
  KV1_NNW paczki z reformatami/mbank_…_kv1_gdn.zip           → 240x400, 250x360, 480x320, 750x100, 750x200, 750x300, 930x180, 980x120
  KV1_NNW paczki z reformatami/mbank_…_kv1_afiliacja.zip     → 300x250, 300x600, 750x100, 750x200, 750x300, 970x200, 970x300
  KV1_NNW paczki z reformatami/mbank_…_kv1_programmatic.zip  → 15 wymiarów
  KV3_NNW paczki z reformatami/…                             → to samo, te same wymiary
  ```
- **Docelowo** (życzenie usera, wprost): Site `CG_GDN`, placement `Display`, **jedna linia
  `linia1`**, ady `{wymiar}_KV1` i `{wymiar}_KV3` — czyli **16 adów** z ośmiu wymiarów GDN.
  Materiały afiliacji i programmatic **nie wchodzą** do zlecenia.
- **Co poszło źle**: agent utworzył ady `120x600_KV1`, `240x400_KV1`, `300x250_KV1` (+ KV3).
  `120x600` i `300x250` to wymiary z paczek **programmatic i afiliacji**. Winy agenta w tym
  mało: `parse_zip` traktował każdy zagnieżdżony zip jako JEDNĄ jednostkę i brał z niego
  **pierwszy napotkany wymiar**, więc cała paczka raportowała `dimensions:
  ['120x600','240x400','300x250']`, a podział na źródła był niewidoczny (`groups: []`).
  Agent dostał trzy wymiary i trzy wymiary zwrócił.
- **Naniesione do kodu** (żeby taka paczka budowała się od razu poprawnie, bez agenta):
  - `parse_zip`: zagnieżdżony zip z **wieloma** wymiarami = PACZKA → jednostka na każdy
    wymiar w środku (`_package_dims`); zip z wymiarem we własnej nazwie (`160x600_gdn.zip`)
    zostaje jedną jednostką, jak dotąd;
  - źródło czytane z **nazwy paczki** (`…_kv1_gdn.zip` → grupa `GDN`, `…_afiliacja.zip` →
    grupa `afiliacja`), więc materiały obcych źródeł nie wpadają do zlecenia GDN;
  - folder `KV{N}_…` = **zestaw materiałów**, jak `linia{N}` → sufiks ada (`240x400_KV1`),
    jedno LP, jedna kreacja; `KV` nie jest wariantem ani folderem strony docelowej;
  - pytanie „które jeszcze kodujemy?" **nie zaznacza już nic domyślnie** (wcześniej
    zaznaczało pierwszą obcą grupę, czyli po cichu wpuszczało obce wymiary);
  - do żądania agenta doszło `zip.by_folder` = wymiary **per folder i zestaw** oraz
    `set_index`/`package` na jednostkach; prompt roli (b) zakazuje brania wymiaru z innego
    folderu i każe rozwijać schemat nazw (`{wymiar}_KV#`) po realnych wymiarach.
- **Sprawdzone**: `tests/test_parse_zip.py` (paczka odtworzona syntetycznie),
  `test_ai_agents.py` (kontrakt `by_folder`), oraz przebieg na realnej paczce:
  16 adów `240x400_KV1 … 980x120_KV3`, jedna kreacja `linia1`, 16 tagów.
- **Wniosek ogólny**: gdy agent oddaje złą strukturę, najpierw sprawdź, **co dostał**.
  Trzy z pięciu jego pytań w tej sesji brały się z tego, że dane wejściowe były błędne.
