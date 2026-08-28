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

## 4. Promocja NNW 08-09.2026 — CAŁA kampania, wszystkie źródła (28.08.2026)

Najpełniejszy przypadek treningowy: **jedna kampania, sześć paczek, sześć źródeł** plus
gotowy arkusz tagów klienta (`Tags_Promocja NNW 08-09.2026`, advertiser `9081506`,
kampania `36424648`) — czyli docelowa struktura jest znana co do nazwy każdego ada.
Materiały: `data/samples/nnw/` (gitignored). Dopasowanie kampanii NIE jest tu tematem —
user wskazał kampanię wprost; tematem jest **budowa struktury**.

Jedna linia dla wszystkiego: kreacja **`linia1`** na każdym Site (mailing ma własne nazwy).
LP te same, rozróżniane parametrami per źródło. Trzy key visuale: **KV1, KV2, KV3** —
KV2 przyszło osobno i później (paczka „linia 2 chlopiec"), stąd inna data w nazwach
placementów programmatica.

### Docelowa struktura z arkusza (89 wierszy tagów trackingowych + 6 placementów serwujących)

| Site | Placement | Ad | Kreacja |
|---|---|---|---|
| `CG_GDN` | `Display` | `{wymiar}_kv{N}` (24) | `linia1` |
| `CG_Demand_Gen` | `Display` | `kv1`, `kv2`, `kv3` (3) | `linia1` |
| `CG_Facebook` | `Video` | `{wymiar}-kv{N}` (9) | `linia1` |
| `CG_Facebook` | `Display` | `{nazwa pliku od wymiaru}_kv{N}` (24) | `linia1` |
| `CG_Facebook` | `Karuzela` | `{wymiar}_karuzela-{karta}_kv2` (8) | `linia1` |
| `WP.pl` | `Display` | `{format}[_v{N}]`, w tym `NativeAd_v1/_v3` (17) | `linia1` |
| `mailsales.pl` | `Mailing` | `mail-1` | `mail-1-CTA`, `mail-1-regulamin` |
| `CG_Programmatic` | `promocja_nnw_08-09.2026_kv{N}_{data}-{audiencja}` (6) | — (serwujący, 15 wymiarów) | — |

### Co narzędzie robi dziś ŹLE (zmierzone, nie oszacowane)

1. **Meta gubi 20 z 28 adów.** `nnw_meta.zip` (kv1+kv3) → narzędzie buduje **8** adów na
   jednym placemencie `Animacje`; arkusz ma **28** na dwóch (`Video` + `Display`).
   Dwie przyczyny, obie w `parse_zip._parse_units`:
   * jednostki są kluczowane `(wymiar, wariant, karta, zestaw)`, więc `1080x1080-a.png`,
     `-b`, `-c`, `-d` i `1080x1080-kv1.mp4` **zwijają się w JEDNĄ** — wariant literowy
     z nazwy pliku nie jest w kluczu;
   * `mp4` i `png` tego samego wymiaru też się zwijają, a mają iść na osobne placementy.
   **Reguła, która odtwarza arkusz dla wszystkich przypadków Meta**: ad = **fragment nazwy
   pliku od wymiaru w dół** (`mBank-uniqa_META_nnw_1200x1200_karuzela-4.jpg` →
   `1200x1200_karuzela-4`; `1080x1080-a.png` → `1080x1080-a`), plus `_kv{N}`, **chyba że
   nazwa już niesie oznaczenie zestawu** (`1080x1080-kv1.mp4` zostaje bez doklejania).
   Sprawdzone na 28 nazwach z arkusza — trafia w każdą.
2. **Placement Meta bierze się z `format_hint` całego zipa**, więc paczka z png i mp4
   dostaje jeden placement `Animacje`. Powinien decydować **typ pliku**: `mp4` → `Video`,
   `png/jpg` → `Display`, folder/nazwa `karuzela` → `Karuzela`.
3. **DemGen: ad = ZESTAW, nie wymiar.** `kv1-demgen/demgen1-{4 wymiary}.png` → arkusz ma
   **jeden** ad `kv1`. Narzędzie buduje cztery (po wymiarach), bo `adKey: "variant"` nie
   ma czego wziąć (folder `kv1-demgen` jest zestawem, nie wariantem) i spada na wymiar.
4. **KV2 nie da się odczytać z nazwy paczki.** `gdn, programmatic NNW Linia chłopiec.zip`
   i `nnw linia 2 chlopiec…zip` nie zawierają słowa `kv2` — zestaw wynika z kontekstu
   zlecenia. Parser daje `set_index=None` i wszystkie ady KV2 wyszłyby bez sufiksu,
   kolidując z KV1/KV3. **Musi być pytanie: „jaki to zestaw?" per paczka.**
5. **Karuzela w zagnieżdżonym zipie gubi karty.** `_package_dims` bierze z paczki same
   wymiary, więc `…_1200x1200_karuzela-4.jpg` traci `karuzela-4`: 2 jednostki zamiast 8.
6. **Nazwa placementu programmatica bierze SUROWĄ nazwę kampanii.** Arkusz ma
   `promocja_nnw_08-09.2026_…`, czyli **znormalizowaną**; dziś wyszłoby
   `Promocja NNW 08-09.2026_…`. Nazwa linii to tam **zestaw** (`kv1`), nie słowo klucza.
7. **`WP.pl`, nie `CG_WP`** — Site w arkuszu nazywa się `WP.pl`; w `source_map.json` mamy
   `CG_WP` (id 6781651). Na koncie testowym istnieją oba, na produkcyjnym użyto `WP.pl`.
8. **WP nie ma materiałów w ogóle.** Zlecenie to komentarz: „LP wp.pl (tu będą potrzebne
   kody pod formaty 970x200, 970x300, 750x300, 750x200, 750x100, 160x600, 300x250,
   300x600 i native ad)". Ady powstają z **listy formatów podanej w zleceniu**, nie z zipa
   — dziś narzędzie nie umie zbudować placementu bez paczki.
9. **Afiliacja i PMAX są w paczkach, ale NIE w arkuszu** — nie są trafficowane w CM.
   Zachowanie narzędzia jest tu poprawne (pytanie „które grupy kodujemy", nic domyślnie).

### Rzeczy, które zadziałały dobrze
- `kv1_nnw_reformaty.zip`: grupy `GDN`/`Programmatic`/`afiliacja`, zestawy `KV1`/`KV3`,
  wymiary — wszystko rozpoznane (to dorobek przypadku 3).
- GDN: `{wymiar}_KV{N}` zgadza się z arkuszem co do struktury (różnica tylko w wielkości
  liter sufiksu: nasz `_KV1` vs klient `_kv1`).
- Mailing: parser znalazł 2 adresy + zaślepkę `#` („sprawdź ofertę" = CTA) i zgłosił ją.
- Programmatic: model obiektów (placement per zestaw × audiencja, 15 wymiarów,
  prospecting + retargeting, bez `default`) potwierdzony arkuszem co do joty.

### Otwarte — do rozstrzygnięcia z użytkownikiem
- WP: skąd `_v1`/`_v2`/`_v3` przy formatach (8 bez sufiksu, 7 z `_v2`, `NativeAd_v1/_v3`)?
  To nie wygląda na KV — brak `160x600_v2` i brak `NativeAd` bez sufiksu.
- Mailing: w paczce są 3 linki (CTA + strona ochronna + słowniczek), w arkuszu 2 kreacje
  (`CTA`, `regulamin`). Który link to `regulamin` i czy arkusz jest deltą?
- Wielkość liter sufiksu zestawu: `_kv1` (arkusz) czy `_KV1` (nasze)?
- Separator w Video (`1080x1920-kv2`) vs Display (`1080x1920_kv2`) — trzymamy niespójność
  klienta czy ujednolicamy?

### Naniesione do kodu (28.08.2026) — zweryfikowane na realnych paczkach
- **`parse_zip._file_tag`**: ogon nazwy pliku od wymiaru w dół wchodzi do klucza jednostki
  (i do klucza `_package_dims`). `nnw_meta.zip`: **4 → 28 jednostek**, karuzela w
  zagnieżdżonym zipie: **2 → 8**. To ta sama poprawka, co przy „zip w zipie" (przypadek 3),
  tylko o poziom niżej: tam gubiliśmy wymiary, tu warianty jednego wymiaru.
- **`build_proposal._ad_name`**: `file_tag` bije wymiar i kartę (niesie oba).
  Nazwy adów Mety zgadzają się z arkuszem klienta co do znaku.
- **`build_proposal._carries_set`**: sufiks zestawu doklejany tylko tam, gdzie nazwa go
  jeszcze nie niesie (`1080x1080-kv1` zostaje). Uwaga: zestaw zapisany samą cyfrą
  (`linia2/` → `2`) jest z tego wyłączony — jako podciąg trafiał w cyfry wymiaru
  (`1` w `160x600`) i po cichu zjadał sufiks.
- **`placementByType`** (config Facebook/Meta) + `build_proposal.placement_by_type()`:
  `mp4` → `Video`, statyki → `Display`. Działa **tylko gdy porcja materiałów miesza
  formaty** — przy paczce jednorodnej zostaje `format_hint`, żeby nietypowy folder nie
  robił własnego placementu.
- **DemGen**: `adKey: "variant"` bierze teraz zestaw, gdy folder wariantu nie występuje →
  `kv1-demgen/` (4 wymiary) to JEDEN ad `kv1`, jak w arkuszu.
- **Etykieta zestawu małymi literami** (`kv1`) — decyzja usera, żeby propozycja była 1:1
  z arkuszem.
- **Placement serwujący**: nazwa kampanii tylko zlowercase'owana ze spacjami na `_`
  (`promocja_nnw_08-09.2026`, myślnik i kropka zostają — `matcher.normalize` zwinąłby je),
  a nazwą linii jest **zestaw**, gdy istnieje. Cztery placementy z `kv1_nnw_reformaty.zip`
  wychodzą **identyczne z arkuszem**, po 15 wymiarów.
- **Ostrzeżenie o kolizji materiałów** zawężone do tego samego WYMIARU — inaczej
  zamierzone zwijanie DemGena zgłaszało cztery fałszywe alarmy.

### Doprecyzowane przez usera (28.08.2026)
- **Folder `Afiliacja/` idzie na Site `WP.pl`**, nie jest pomijany: „kreacje w folderze
  afiliacja będą pod WP.pl". Zgadza się co do sztuki — 7 wymiarów tego folderu to
  dokładnie 7 adów `_v2` w arkuszu. To decyzja PER ZLECENIE z komentarza, nie stała
  reguła configu, więc user musi móc wskazać źródło dla grupy w UI.
- **`_v2` przy formatach WP = drugi zestaw** (nowa linia kreacji „chłopiec"); ady bez
  sufiksu to pierwszy rzut materiałów.
- **`NativeAd_v1` / `NativeAd_v3` to WERSJE formatu NativeAd** wg specyfikacji WP
  (NativeAd występuje w 3 wersjach), a nie zestawy — mimo identycznego zapisu `_v{N}`.
  **Otwarte**: skoro wersje są trzy, dlaczego w arkuszu są tylko `v1` i `v3`?
- **Zestaw dla paczki bez oznaczenia w nazwie** (KV2) ma być podany w **komentarzu
  zlecenia** („materiały z _kv2 analogicznie do pozostałych"), a nie osobnym polem
  formularza — czyli narzędzie musi go stamtąd odczytać.

### Wynik: propozycja porównana z arkuszem klienta ad po adzie (28.08.2026)
Po naniesieniu powyższych poprawek narzędzie buduje z tych samych paczek dokładnie tę
strukturę, którą trafficker zrobił ręcznie — **zero brakujących i zero nadmiarowych adów**:

| Site / placement | arkusz | narzędzie | różnice |
|---|---|---|---|
| `CG_GDN` / `Display` (kv1+kv2+kv3, dwie paczki scalone) | 24 | 24 | brak |
| `CG_Facebook` / `Display` (kv1+kv3) | 22 | 22 | brak |
| `CG_Facebook` / `Video` (kv1+kv3) | 6 | 6 | brak |
| `CG_Demand_Gen` / `Display` (kv1+kv3) | 2 | 2 | brak |
| `CG_Programmatic` (placementy serwujące, kv1+kv3) | 4 | 4 | brak, po 15 wymiarów |

Kreacja wszędzie `linia1`, zgodnie z arkuszem. KV2 wchodzi z komentarza zlecenia
(`set_from_message`), formaty WP z jego treści (`formats_from_message`).

### Dwie drogi materiałów WP (ustalenie usera — afiliacja NIE jest wiązana z WP)
1. folder w paczce nazwany `WP` — reszta paczki zostaje przy swoich źródłach;
2. samo źródło WP + paczka z JEDNYM folderem — folder nie staje się grupą (nie ma obok
   czego być obcym), więc materiały idą na źródło zlecenia, jakkolwiek folder się nazywa.

Obie działały bez zmian w kodzie; są teraz przykryte testami, żeby nie zniknęły. Folder
`Afiliacja/` stojący OBOK rozpoznanego (`GDN/`) pozostaje obcą grupą do decyzji
użytkownika — świadomie, bo powiązanie afiliacji z WP było jednorazowe.
