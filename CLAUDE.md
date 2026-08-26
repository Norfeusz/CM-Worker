# CM Worker — kontekst projektu dla agenta

Ten plik jest wczytywany automatycznie na starcie każdej sesji Claude Code w tym katalogu.
Jeśli właśnie zaczynasz nową sesję po przekazaniu kontekstu — to jest Twój punkt startowy.
Dodatkowo powinieneś mieć dostęp do systemu pamięci (auto memory) tego projektu — pliki
`cm360-object-model.md`, `cm360-zip-and-tag-formats.md`, `cm-worker-future-ai-agents.md`,
`feedback-model-choice.md` w katalogu pamięci — zawierają jeszcze więcej technicznego detalu
i historii decyzji. Ten plik jest ich uporządkowanym streszczeniem + mapą "co dalej".

## Co to za narzędzie

Interfejs do trafficowania w **Campaign Manager 360** (CM360) dla agencji (konto Cube Group,
klient docelowy: mBank). Z **linku LP + zipa z kreacjami + (opcjonalnie) wiadomości zlecenia**
narzędzie: dopasowuje advertisera/kampanię/linię, parsuje strukturę zipa, buduje **edytowalną
propozycję** Site→Placement→Ad→Creative, pozwala ją ręcznie poprawić, zapisuje do CM360 i generuje
tagi trackingowe (eksport `.xls` w formacie zgodnym z tym, co dotąd traffickerzy robili ręcznie).

CM360 jest tu używany **wyłącznie do trackingu/tagów** — kreacje to proste szablony 1×1
(`TRACKING_TEXT`), bez uploadu realnych assetów (poza przyszłym przypadkiem programmatic).
Jeden tag = jedna trójka (Placement × Ad × Creative).

## 🔴 BEZPIECZEŃSTWO — zasady, których nie wolno łamać bez pytania użytkownika

1. **Konto testowe vs produkcja.** Produkcja to konto **MBank** (profil CM360 `9765911`,
   account `424605`) — **twardo zablokowane w kodzie** (`scripts/cm_auth.py`, allowlist).
   Wszystkie testy/zapisy dzieją się na koncie **Cube Group** (profil `9556074`), advertiser
   testowy **`11992166`** ("Reklamodawca testowy - na potrzeby szkolenia"). **Nigdy nie
   rozszerzaj allowlisty bez wyraźnej prośby użytkownika** — to jedyna rzecz chroniąca
   produkcyjne konto klienta przed przypadkowym zapisem.
2. **Guard w kodzie, nie w scope'ie OAuth.** CM360 nie ma osobnego scope'a read-only dla
   traffickingu, więc bezpiecznik jest wymuszony w `cm_auth.py`: GET zawsze przechodzi;
   POST/PUT tylko gdy `service(read_only=False)` **i** profil/advertiser są na allowliście
   (sprawdzane i w URI, i w `advertiserId` ciała żądania); **DELETE nigdy** (i tak CM360 go
   nie ma dla większości obiektów).
3. **Zawsze dry-run przed realnym zapisem.** Orkiestrator (`scripts/orchestrate.py`) i UI
   (`Zatwierdź strukturę` → pokazuje plan → dopiero `Wykonaj w CM360`) są zaprojektowane tak,
   żeby zawsze najpierw pokazać co się stanie, zanim cokolwiek się zapisze naprawdę.
4. Zanim zdejmiesz/rozszerzysz jakikolwiek z powyższych bezpieczników — zapytaj użytkownika
   wprost. To nie jest sugestia, to twardy wymóg tego projektu.

## Jak uruchomić / testować

Pełna instrukcja jest w [README.md](README.md) — tam znajdziesz dokładne komendy. W skrócie:

```bash
py scripts/serve.py        # backend stdlib (bez npm) — serwuje UI + całe API
```
```bash
start http://127.0.0.1:8765/
```

**Normalnie uruchamiaj przez `start.bat`** (dwuklik) — gitignorowany, user ma go u siebie;
szablon to `start.example.bat`. Ustawia `N8N_STRUCTURE_URL`, `N8N_INTENT_URL`, `N8N_TOKEN`,
przestawia konsolę na UTF-8 (`chcp 65001`, inaczej polskie znaki się sypią) i startuje
`py -u scripts\serve.py --open`. Flaga `--open` sprawia, że **serwer sam otwiera
przeglądarkę** gdy zacznie nasłuchiwać — nie robi tego `.bat`, bo tylko serwer wie, kiedy
gniazdo jest gotowe (opóźnienie w `.bat` było wyścigiem przy zimnym starcie).
Zamknięcie okna konsoli zatrzymuje serwer. **Zmienne czyta na starcie**, więc po edycji promptów w `ai_agents.py`
konieczny jest restart (moduł siedzi w pamięci procesu; objawia się identyczną odpowiedzią
po poprawce).

**Nie stawiaj `serve.py` jako swojego zadania w tle** — trzy razy padł, bo jego czas życia
jest powiązany z zadaniami agenta, nie z sesją użytkownika. Poproś użytkownika o dwuklik na
`start.bat`; własny proces stawiaj tylko na czas konkretnej weryfikacji.
Testy offline: `py tests/test_matcher.py`, `test_proposal.py`, `test_orchestrate.py`,
`test_create_site.py`, `test_ai_agents.py`, `test_export_tags.py`, `test_parse_zip.py`
(**368/368 zielone na 10.08.2026** — uruchom je jako PIERWSZY krok sesji, żeby potwierdzić,
że nic się nie popsuło). Rozkład: matcher 71, proposal 91, orchestrate 44, create_site 15,
ai_agents 104, export_tags 23, parse_zip 20.
`test_parse_zip.py` buduje paczki w locie (`zipfile` w temp), więc testuje realne kształty
dostaw bez trzymania plików klienta w repo.
`test_ai_agents.py` stawia udawany webhook n8n na localhoście, więc testuje też transport
i odrzucanie złych odpowiedzi — bez sieci i bez klucza API.

**Site `CG_Demand_Gen` id=`10795500`** (wpis katalogu `6007148`) — powstał niezamierzenie przy
weryfikacji `/api/create-site` (skrypt kontrolny wysłał `dryRun:false` w oczekiwaniu odmowy, a
wpis katalogu o tej nazwie istniał, więc zapis przeszedł). **Użytkownik zaakceptował go
świadomie — to prawidłowy Site dla źródła DemGen, nie śmieć; nie usuwać.** Dzięki temu
wszystkie źródła z `source_map.json` mają dziś swój Site na koncie (patrz `_verified` w tym
pliku), czyli żadne standardowe zlecenie nie wymaga już tworzenia Site.

**Node — NAPRAWIONE (04.08.2026). `npm` NIGDY nie był zepsuty, był ZASŁONIĘTY.**
Poprzednia diagnoza w tym pliku („npm jest zepsuty, EPERM na `C:\Users\admin1`") była
myląca. Prawdziwa przyczyna: nvm-for-windows zainstalowano na koncie **`admin1`**, a w
**systemowym** PATH siedzą dwa wpisy w jego profil — `C:\Users\admin1\AppData\Local\nvm`
i `C:\nvm4w\nodejs` (symlink → `…\admin1\…\nvm\v14.21.3`). Windows przetwarza PATH
systemowy **przed** użytkownika, więc `npm` zawsze trafiał na ten symlink; npm rozwiązuje
ścieżkę swojego modułu i robi `lstat` na `C:\Users\admin1`, czego zwykłe konto nie może →
`EPERM`. Rozpakowany Node w profilu użytkownika działał cały czas bez zarzutu.

Systemowego PATH **nie da się** poprawić bez admina (user go nie ma) ani nadpisać wpisem
użytkownika (jest później w kolejności). Dlatego kolejność wymuszamy **per sesja**:

```powershell
. .\node-env.ps1            # najnowszy dostępny Node (dziś 24.19.0, npm 11.17.0)
. .\node-env.ps1 -Version 14  # powrót na 14.21.3 (npm 6.14.18), gdy coś tego wymaga
```

`node-env.ps1` wykrywa wersje z katalogów `nodejs-*` w profilu użytkownika, więc jest
przenośny. **Uruchamiaj z kropką** — bez niej zmiana PATH ginie z podprocesem.
Zainstalowane: `~\nodejs-24\node-v24.19.0-win-x64` (ZIP z nodejs.org, suma SHA256
zweryfikowana) oraz **nietknięty** `~\nodejs-14.21.3\…` — user chce móc wrócić na 14.
Registry npm sprawdzone: `npm ping` OK, `npm view vite version` → 8.2.0, bez proxy.

**Docelowa naprawa: poprosić IT o usunięcie obu martwych wpisów `admin1` z systemowego
PATH.** Wtedy `node-env.ps1` przestanie być potrzebny.

UI to nadal samowystarczalny React bez builda (Babel w przeglądarce) serwowany przez
`scripts/serve.py` — ale **to już nie jest wymuszone środowiskiem, tylko nieodrobioną
pracą**. Build jest odblokowany. Uwaga przy planowaniu: to już JEST React 18 (UMD z CDN),
więc „przejście na React" oznaczałoby tylko dodanie builda i rozbicie
`ui/index.html` (1057 linii) na komponenty — nie zmianę technologii. Poprawki CSS/JSX
przenoszą się do builda 1:1, więc nie ma powodu ich wstrzymywać.

## Architektura / mapa plików

```
scripts/
  cm_auth.py        # OAuth + bezpiecznik (allowlist profil/advertiser, GET-only/writes-scoped)
  cm_read.py         # odczyt stanu z CM360 (sites/placements/ads/creatives/LP), search_sites, site_structure
  cm_tree.py         # samodzielna read-only przeglądarka drzewa CM360 (CLI)
  matcher.py         # CZYSTY rdzeń: link -> advertiser/kampania/linia + wykrywanie konfliktów (testowalny bez API)
  match_link.py      # matcher.py podpięty pod żywe API (tryb --test rutuje na konto testowe)
  build_proposal.py  # parser+matcher+source -> kontrakt propozycji (drzewo + questions + tags)
  orchestrate.py     # zapis: LP -> rejestracja w kampanii -> site -> creative -> assoc -> placement -> ad
  cm_write.py        # niskopoziomowe insert/update do CM360 (dry_run=True domyślnie)
  export_tags.py     # generatetags -> .xls (format identyczny z ręcznymi eksportami tradera)
  ai_fallback.py     # wykrywanie eskalacji (niska pewność) + kontrakt żądania + seam interpret()
  ai_agents.py       # DWIE ROLE AGENTÓW: prompty, schematy, walidacja, transport do n8n,
                      #    deterministyczne stosowanie operacji (apply_ops) — patrz sekcja niżej
  serve.py           # backend: serwuje ui/ + całe REST API (patrz niżej) — GŁÓWNY punkt wejścia
                      #    friendly_error(): surowe wyjątki -> komunikaty z działaniem (brak
                      #    sieci/VPN, timeout, TLS, wygasły token OAuth, HttpError z CM360)
  demo.py            # CLI: cały pipeline end-to-end bez UI (do szybkich testów/debugowania)
  plan_writes.py     # ⚠️ LEGACY — wczesny prototyp planera, ZASTĄPIONY przez orchestrate.py.
                      #    Nic go nie importuje. Bezpiecznie zignorować lub usunąć.
parser/parse_zip.py  # analiza zipa: wymiary, warianty, typy, wykrywanie wielu grup źródłowych
config/
  advertiser_map.json  # URL (człony ścieżki) -> advertiser; 14 advertiserów produkcyjnych (prototyp)
  source_map.json      # source (GDN/Meta/Facebook/...) -> Site + konwencja nazw placementów + adKey
ui/index.html        # cały frontend (React bez builda, Babel-in-browser)
tests/               # testy offline (matcher/proposal/orchestrate) — URUCHOM JE NA START SESJI
docs/n8n-ai-architecture.md  # projekt integracji n8n + Agent AI (jeszcze niezaimplementowany)
data/
  training_examples.md   # log przykładów treningowych (zip+wiadomość -> docelowa struktura)
  samples/                # przykładowe zipy klienta (gitignored — prawdziwe materiały, nie commitować)
  *.xls, proposal_demo.json  # artefakty robocze (gitignored)
credentials/         # token OAuth + client_secret (GITIGNORED — nigdy nie commitować)
ui/cubegroup-logo.svg  # oryginalny asset z motywu cubegroup.pl (wariant z białym napisem)
node-env.ps1         # wymusza kolejność PATH dla Node w BIEŻĄCEJ sesji (patrz sekcja o Node)
start.bat            # GITIGNORED (webhooki + token). Dwuklik = serwer + przeglądarka
start.example.bat    # szablon dla nowego stanowiska
```

### Endpointy `scripts/serve.py` (GET serwuje też statyczne pliki z `ui/`)
- `POST /api/build-proposal` — `{link|links[], keywords[]?, source, sources[]?,
  linkSources{}?, message, zipB64|zipPath, campaignId?, newCampaign?, folderMap?}` →
  kontrakt propozycji. `sources[]` = wszystkie źródła zlecenia (główne = `source`),
  `linkSources` = `{indeksAdresu: źródło}` (patrz sekcja „Kilka źródeł").
  `links[]` = kilka LP w jednym zleceniu (wszystkie do TEJ SAMEJ kampanii; różni advertiserzy
  są odrzucani). `keywords[]` = słowo klucza usera dla każdego adresu (pozycyjnie), patrz
  sekcja o konwencji nazw. **Deduplikacja adresów dzieje się w `matcher.dedupe_links`
  PRZED czymkolwiek** — wszystko dalej (słowa klucza, etykiety folderów) jest keyowane
  pozycją, więc zwinięcie powtórzonego adresu później przesunęłoby te dane o jeden. `folderMap` = odpowiedzi usera „folder → który LP" (`"0"`/`"1"`/`"all"`),
  echoowane w `lpFolders.override`, żeby UI nie trzymał własnego ukrytego stanu.
  `campaignId` to override, gdy user ręcznie wybrał kampanię; `newCampaign` to nazwa nowej.
- `POST /api/apply-suggestions` — `{proposal, suggestions}` → sugestie roli (a) tłumaczone na
  operacje roli (b) i stosowane tym samym `apply_ops`, z jednym diffem. `notes` mówi, czego
  **nie** przetłumaczono i dlaczego (`ad_naming` i `group_mappings` są celowo pomijane)
- `GET /api/tags-file?name=` — pobranie wygenerowanego arkusza `.xls` z `data/`. Wpuszcza
  wyłącznie `basename` z rozszerzeniem `.xls` (próby `../credentials/token.json` → 404)
- `POST /api/refine` — `{proposal, answers, remarks}` → **Agent (b)**: uwagi → operacje z n8n →
  `ai_agents.apply_ops` stosuje je deterministycznie → `{proposal, log, applied, skipped, unclear}`
- `POST /api/assist` — `{proposal}` → **Agent (a)**: podpowiedzi do struktury (`ai.request`
  z propozycji leci do n8n). Zwraca **tylko sugestie**, drzewa nie rusza
- `POST /api/commit` — `{proposal, dryRun}` → uruchamia orkiestrator (dry-run albo realny zapis + eksport tagów)
- `GET /api/sites?q=` — kaskada wyszukiwania site (konto → Site Directory), jak natywny dialog CM
- `POST /api/create-site` — plan (dry-run) dodania nowego site
- `GET /api/site-structure?campaignId=&site=` — istniejące placementy/ady/creative; karmi
  pickery „dodaj istniejący…" **oraz** zakładkę „Obecna struktura kampanii"
- `GET /api/campaigns` — wszystkie kampanie advertisera testowego (przeglądarka kampanii przy braku dopasowania)
- `GET /api/campaign-lps?campaignId=` — strony docelowe danej kampanii (leniwie ładowane w UI)

## Agenci AI (n8n)

**Kierunek ruchu jest odwrotny niż w `docs/n8n-ai-architecture.md`:** `serve.py` **woła n8n**
wychodząco, a nie n8n Pythona. Dwa powody, oba twarde: n8n na serwerze firmowym nie dosięgnie
`127.0.0.1:8765` na laptopie traffickera, a bezpiecznik `cm_auth` musi zostać w Pythonie —
n8n nie ma i nie potrzebuje żadnego dostępu do CM360.

**Podział odpowiedzialności:** prompty i schematy wyjścia żyją w repo (`scripts/ai_agents.py`),
n8n trzyma **klucz API, wybór dostawcy i wybór modelu** (węzeł „Zbuduj żądanie”). Workflow jest
cienkim przekaźnikiem — cała wartość n8n tutaj to custody klucza (nie ląduje na laptopie) +
centralne logi, nie orkiestracja. Nie udawajmy inaczej.

**Kod jest agnostyczny wobec dostawcy.** `n8n/` ma dwa workflow o identycznym kontrakcie
webhooka: `cm-worker-agent-gemini.json` (Gemini, `x-goog-api-key`, endpoint
`/v1beta/interactions`, wynik w `output_text`) i `cm-worker-agent.json` (Anthropic,
`x-api-key`, `/v1/messages`, wynik w `content[]`). Zmiana dostawcy = podmiana workflow, **zero
zmian w Pythonie**. Zweryfikowane: Gemini przyjmuje standardowy JSON Schema (typy tablicowe
`["string","null"]`, `additionalProperties`), więc `STRUCTURE_SCHEMA`/`INTENT_SCHEMA` lecą do
obu dostawców bez tłumaczenia dialektu.

- **Rola (a)** `STRUCTURE_SCHEMA` — podpowiada mapowania w punktach niskiej pewności. Zwraca
  **sugestie do zatwierdzenia**, nigdy nie zmienia drzewa.
- **Rola (b)** `INTENT_SCHEMA` — interpretuje uwagi użytkownika i zwraca **listę operacji
  edycyjnych** (`OPS`), nie przepisane drzewo. Świadome odejście od notatki w pamięci („zwróci
  poprawioną strukturę”): model oddający całe drzewo może po cichu zgubić węzeł i nie ma czego
  recenzować. Lista operacji jest walidowalna schematem, stosowana deterministycznie przez
  `apply_ops`, pokazywana userowi jako diff i każda operacja ma odpowiednik w ręcznym UI.
- **Wyjście wymuszone strukturalnie** (`output_config.format` = `json_schema`) — model
  fizycznie nie może zwrócić innego kształtu. `ai_agents.validate` to druga linia obrony.
- **`apply_ops` adresuje węzły przez placement+ad, nigdy po samej nazwie** — dopasowanie po
  nazwie raz już przemianowało sąsiedni creative i ten błąd nie ma wrócić przez ścieżkę AI.
- Konfiguracja: `N8N_STRUCTURE_URL`, `N8N_INTENT_URL`, `N8N_TOKEN` w środowisku. Bez nich
  narzędzie działa normalnie, a przyciski AI mówią „nie podpięte” (`ai.wired`).
- **Zero nowych zależności** — transport to `urllib`, walidacja schematu własna (stdlib),
  bo `serve.py` ma być uruchamialny bez pip install.

### Zwalidowane na żywo (30.07.2026, Gemini `gemini-3.5-flash` przez n8n Cloud)
Obie role przetestowane end-to-end na prawdziwym modelu. Rzeczy, które kosztowały debugging:

1. **Surowa odpowiedź Interactions API NIE ma pola `output_text`** — to akcesor SDK.
   Tekst leży w `steps[]` → krok `type=="model_output"` → `content[]` → `{type:"text", text}`.
   **Przed `model_output` może wystąpić krok `thought`**, więc szukaj kroku po typie, nigdy
   po indeksie. Pole `status` (`completed`/`incomplete`/`failed`/…) rozstrzyga niepowodzenie.
2. **`respondWith: "json"` + `{{ JSON.stringify($json) }}` w węźle „Respond to Webhook"
   zwracało pustą odpowiedź 200.** Użyj `respondWith: "firstIncomingItem"` — bez wyrażenia,
   więc nie ma czego źle zinterpretować.
3. **Prompt musi jawnie podawać konwencję nazw LP**, inaczej model wstawia audience tam,
   gdzie ma być źródło. Obowiązująca konwencja (patrz sekcja „Konwencja nazw" niżej):
   LP = `linia{N}-{ŹRÓDŁO}[-{słowo rozróżniające}]`, creative = `linia{N}[-{audience}]`.
   Potwierdzone żywymi danymi konta (`linia1-FB`, `linia2-GDN`).
4. **Zmiana promptu wymaga restartu `serve.py`** — moduł jest zaimportowany w pamięci
   procesu, edycja pliku nie działa na żywo. Objawia się identyczną odpowiedzią po poprawce.
5. Model potrafi być nadgorliwy: przy zipie bez podfolderów zwracał `group_mappings` z pustym
   `folder`. Prompt tego zakazuje (bo taka reguła trafiłaby do configu przez promocję i
   zostałaby tam na zawsze) — poprawka zweryfikowana, pole wraca teraz puste.
6. **Rola (b) musi widzieć zawartość zipa.** `build_intent_request` wysyłał tylko `remarks`
   + `structure` (nazwy węzłów). Na uwagę „wymiary zgodnie z zawartością paczki zip" agent
   **słusznie** zwrócił zero operacji z notatką, że nie ma tych danych — zachował się
   poprawnie na niepełnym kontrakcie. Teraz dokładany jest `zip` z `ai.request.zip` (leży już
   w propozycji, odłożony dla roli (a) — bez ponownego uploadu i parsowania). Prompt mówi
   też, że **nowo utworzony placement jest PUSTY**, więc jeśli uwagi implikują zawartość,
   agent ma dorzucić `add_ad` od razu, a nie pytać w drugiej rundzie. Po poprawce na żywym
   Gemini: paczka GIF/HTML/PNG + uwagi użytkownika → **14 operacji, 0 pominiętych**.
7. **Generyczna koperta operacji WYMAGA tabeli pól w promptcie.** `INTENT_SCHEMA` ma jeden
   kształt operacji (`placement/ad/creative/name/to/lpName/lpUrl`), więc schemat nie jest w
   stanie wymusić, które pole znaczy co przy której operacji. Bez jawnej tabeli Gemini
   rozumiał zlecenie poprawnie, ale wstawiał nazwy w inne pola i **3 z 4 operacji były po
   cichu pomijane** — z notatką modelu twierdzącą, że wszystko wykonał. Prompt ma teraz
   tabelę „FIELD USAGE PER OP", a `apply_ops` toleruje oczywiste przestawienia
   (`add_placement` przyjmie nazwę z `placement`/`to`, `move_ad` cel z `name`). Test
   regresyjny w `test_ai_agents.py`. **Wniosek na przyszłość: testy na atrapie webhooka nie
   wyłapią tej klasy błędu, bo odpowiedzi atrapy pisze się pod własne założenia** — po każdej
   zmianie kontraktu operacji trzeba jeden przebieg na żywym modelu.

## Konwencja nazw LP i creative (ZMIENIONA 05.08.2026 — kolejność jest wymogiem)

```
Landing page:  linia{N}-{ŹRÓDŁO}[-{słowo rozróżniające}]     linia2-GDN, linia1-Facebook-lookalike
Creative:      linia{N}[-{słowo rozróżniające}]              linia2, linia1-lookalike
```

**Numer linii, potem ŹRÓDŁO, a słowo rozróżniające na końcu i tylko gdy istnieje.**
Creative **nie nosi źródła** — źródło jest wyłącznie w nazwie LP.

Poprzednio etykieta stała w środku (`linia1-lookalike-Facebook`). To nie była tylko
kwestia gustu: przy takiej kolejności **nie dało się odczytać źródła z nazwy**, bo stało
na końcu za nieznaną liczbą segmentów. Na tym przewracało się `detect_line_conflict`,
które porównuje źródło istniejącego LP z wybranym w UI — dla LP z etykietą nie wykrywało
konfliktu w ogóle. Po zmianie źródło to zawsze pierwszy segment po numerze, więc jest
jednoznaczne (test regresyjny w `test_matcher.py`).

Konwencja żyje w **jednym miejscu**: `matcher.lp_name()`, `matcher.creative_name()`,
`matcher.split_lp_name()`. Nie buduj tych nazw ręcznie w innych plikach — prompt roli (b)
opisuje ją słownie i też został zaktualizowany. (Wyjątek świadomy: `NewLineEditor` w UI
podpowiada nazwę creative z nazwy LP tym samym wzorcem — to tylko **podpowiedź** w polu,
które user może nadpisać, więc rozjazd nie może niczego po cichu zepsuć.)

**ŹRÓDŁO w nazwie to SKRÓT z configu, nie klucz źródła** (06.08.2026). `lpSource`
w `source_map.json`: Facebook i Meta (dwa klucze, jeden Site) → `FB`; brak wpisu = klucz
źródła jak dotąd (`GDN`). Czytane przez `build_proposal.lp_source()`; `serve` podaje skrót
do `resolve_lines` i `detect_line_conflict`, a pełny klucz zostaje do Site/placementów/adKey.
Naprawia to przy okazji wykrywanie konfliktu linii, które porównywało „Facebook" z „FB"
z żywego konta i **nigdy nie mogło trafić**.

**SŁOWO KLUCZA per adres** (06.08.2026, życzenie usera). Osobne okienko przy każdym LP
w UI; `matcher.keyword_label()` sanityzuje (spacje→myślnik, interpunkcja wycięta, wielkość
liter i polskie znaki ZOSTAJĄ — to nazwa wybrana przez człowieka, nie forma do porównań).
Słowo klucza **bije każdą etykietę automatyczną i jest stosowane zawsze**, także dla
jednego adresu bez kolizji. Tym zamknięty jest punkt 12 kolejki (`utm_campaign=
refinansowanie2026` dawał `linia2-GDN-refinansowanie2026`, user chciał `refinans`).
Wzmacnia też dopasowanie folderów zipa do LP (folder `Lookalike/` ↔ słowo `lookalike`).
Jedno ograniczenie, jawnie raportowane w UI (`line.keywordIgnored`): gdy adres JUŻ jest
LP w kampanii, słowo klucza jest odrzucane — `_ensure_lp` szuka po nazwie, więc
przemianowanie utworzyłoby drugie LP na ten sam adres.

## Kilka ŹRÓDEŁ w jednym zleceniu (06.08.2026)

Paczka rozdzielona folderami źródeł (`GDN/` + `Programmatic/`) jest trafficowana raz.
W UI: lista „Źródło" to źródło **główne**, obok checkboxy „Dodatkowe źródła w tej paczce",
a przy każdym adresie dochodzi lista źródeł. Kontrakt: `sources[]` + `linkSources{}`.

Rozstrzygnięcia (decyzje usera, nie moje domysły):
* **LP per źródło, ten sam numer linii** — `linia1-GDN` obok `linia1-Programmatic`,
  najczęściej ten sam adres różniący się tylko parametrami. Creative zostaje JEDEN
  (`linia1` — bez źródła), więc placementy obu źródeł linkują tę samą kreację, ale
  każdy do LP swojego źródła. Źródło bez własnego adresu bierze adres źródła głównego.
* **Foldery `linia1/`, `linia2/` w paczce to NIE strony docelowe**, a dwa komplety tych
  samych wymiarów: jedno LP, rozróżnienie na adzie (`300x250_1`, `300x250_2`).
  `parse_zip._set_index()` + sufiks w `build_proposal`. Wcześniej parser zwijał oba
  komplety w jeden ad i **połowa materiałów przepadała bez śladu**.

Mechanika (rzeczy, które się na tym wywracały):
* **Orkiestrator brał JEDEN Site** (`proposal["site"]`) dla wszystkich placementów i
  ignorował `pl["site"]` — czyli poprawnie zbudowane drzewo dwóch źródeł zapisałoby się
  na jednym Site. Teraz Site jest rozstrzygany per placement (`site_of`), a klucze
  `state["placements"]`/`state["ads"]` i tak są per Site, więc ad o tej samej nazwie na
  drugim Site powstaje na nowo, zamiast „REUSE" cudzego.
* **Folder źródła nie może być kandydatem na folder LP.** Adresy nosiły
  `utm_source=gdn` / `=programmatic`, więc `match_folders_to_lps` przypisał `GDN/` do
  LP1, a `Programmatic/` do LP2; oba zostały „zużyte" jako rozróżnienie LP, przestały być
  grupami źródeł i **drugie źródło zniknęło z drzewa bez słowa**. Wyłapane dopiero na
  żywym teście w przeglądarce — offline wyglądało dobrze.
* **`resolve_lines` dedupuje po (adres, źródło)**, nie po samym adresie; rodzeństwem
  wymagającym etykiety są wpisy o tym samym numerze **i** źródle. Istniejące LP na tym
  adresie jest używane tylko gdy jego nazwa niesie TO SAMO źródło.
* **`folderMap` jest keyowany ADRESAMI**, a `lines` ma wpis na (adres × źródło) — stąd
  `line_addresses` w `build_proposal`/`build_questions`.
* Ten sam Site + ta sama nazwa placementu = jeden węzeł w propozycji (scalanie w
  `build_proposal`); wcześniej folder źródła obok materiałów luzem dawał dwa identyczne.

Programmatic jest tu obsłużony jak zwykłe źródło trackingowe (1×1). **Realny upload
assetów dla programmatic nadal NIE jest zrobiony** — patrz punkt 7 kolejki.

## Model domenowy (zwalidowany na żywych danych CM360)

| Pojęcie kliencie | Obiekt CM360 | Uwagi |
|---|---|---|
| advertiser | **Advertiser** | dopasowanie po członach URL (pomijając prefiks `/lp2/2026/c1/`) |
| kampania | **Campaign** | LP żyją NA POZIOMIE kampanii (nie advertisera), nie są współdzielone między kampaniami |
| source (GDN/Meta/FB/DemGen/Programmatic/Mailing) | **Site** | `CG_GDN`, `CG_Facebook`, `CG_Demand_Gen`, ... — `Meta` to alias `Facebook` |
| format | **Placement** | zawsze `compatibility=DISPLAY`, `size=1x1`; nazwa zależy od source (GDN→`Display`, FB→`Link`/`Animacje`/`Karuzela`/`Posty`, DemGen→`Display`+`Karuzela`) |
| wymiar/wariant | **Ad** (nazwa) | GDN=wymiar (`300x250`), DemGen=wariant (`demgen1/2/3`, wymiar ignorowany), FB karuzela=`{wariant}_{wymiar}_{karta}` |
| linia (link+audience) | **Creative** (nazwa) | `linia3`, `linia4-slonce`, `refinans-prospecting` — **jeden Ad może mieć WIELE creative** |

**Kluczowa zasada:** jeden tag = jedna trójka (Placement, Ad, Creative). Liczba tagów = adów × creative na nich.
Nazwy Ad/Placement pochodzą z konwencji zip+source; linie/audience pochodzą z **wiadomości zlecenia**, nie z zipa.

## Zwalidowane fakty o API CM360 (rzeczy, które kosztowały nas realny debugging)

1. **API to `dfareporting` v5** (v4 zostało wycofane).
2. `placements.list`/`ads.list` używają `campaignIds` (liczba mnoga), `creatives.list` używa `campaignId` (l. poj.) — łatwo się pomylić.
3. **LP musi być zarejestrowana w liście "Stron docelowych" kampanii**, zanim zadziała — samo wskazanie jej przez `clickThroughUrl` Ada NIE wystarczy (LP wisi "w powietrzu", nie pojawia się w drzewie kampanii). REST v5 nie ma osobnego pola/zasobu do tego poza `defaultLandingPageId`. **Odkryty trik**: `PATCH campaign defaultLandingPageId=nowyLP` a potem z powrotem na oryginał — nowy LP **zostaje** na liście kampanii (`cm_write.add_lp_to_campaign`). Pierwsza linia nowej kampanii zostaje jako prawdziwy default; kolejne używają cyklu.
4. **Creative musi być powiązany z kampanią** (`campaignCreativeAssociations.insert`) **zanim** jakikolwiek Ad może go użyć.
5. **`ads.insert` — `startTime` nie może być w przeszłości** (błąd 12029) — używamy `now+5min`.
6. **`placements.insert` wymaga `tagFormats`** (min. jeden zgodny z typem, inaczej błąd 11032) — używamy pełnego zestawu DISPLAY (patrz `cm_write.placement`).
7. **`placements.generatetags` to POST** — wymaga `service(read_only=False)`, mimo że koncepcyjnie to odczyt.
8. **Site Directory (`directorySites.list`) jest OGROMNY** — zawsze z `searchString` + `maxResults`, inaczej próba pełnej paginacji wisi.
9. Dodanie nowego Site = `directorySites.insert` (jeśli nie istnieje w katalogu) → `sites.insert` (podpięcie do konta) — mirror natywnego dialogu CM "Select a site for your placement".
10. **Zapisy na poziomie KONTA nie są chronione allowlistą advertisera.** `Site`,
   `DirectorySite` (i inne obiekty account-level) nie mają `advertiserId` w body, więc
   `_check_body` ich nie widzi — jedyną barierą jest allowlista profilu. Przy pracy z Site
   trzeba więc uważać ręcznie: `dryRun` domyślnie True i pytać użytkownika przed zapisem.
   `sites` nie ma DELETE (tylko get/insert/list/patch/update) — utworzonego Site nie da się
   usunąć przez API, więc pomyłka zostaje na koncie na stałe.
11. Tagi to standardowe DoubleClicki generowane PRZEZ CM360 (`trackimp/…;dc_trk_aid={adId};dc_trk_cid={creativeId}`), **nie konstruujemy ich sami** — tylko wołamy `generatetags` i eksportujemy.

## Co już działa (pełny pipeline, zweryfikowany end-to-end na żywym koncie testowym)

- ✅ Dopasowanie link → advertiser/kampania/linia (+ wykrywanie konfliktu linii, np. różnica tylko w `sprzedawca`)
- ✅ Parser zipów (8+ realnych wzorców, wykrywanie wielu folderów źródłowych typu GDN+Screening)
- ✅ Proposal builder z `questions` (pytania sterujące: które grupy kodować, reuse czy nowa linia)
- ✅ Pełny zapis do CM360 (LP→rejestracja→site→creative→assoc→placement→ad) z REUSE/CREATE/NO-OP/UPDATE
- ✅ Eksport tagów `.xls` jako delta (tylko nowa/edytowana linia), format identyczny z ręcznym
- ✅ UI: formularz zlecenia (link+zip+source+wiadomość), drzewo z pytaniami, drag&drop, edycja nazw
- ✅ Ręczna edycja: dodawanie placementów/adów (z listy istniejących lub nowe), wiele creative na Ad,
  własny LP per creative, przycisk „zastosuj do wszystkich" (dodaje/przemianowuje creative na wszystkich adach)
- ✅ Przeglądarka kampanii (lista + rozwijane LP + „Użyj tej kampanii") — wspólny komponent
  `CampaignPicker` używany w DWÓCH miejscach: przy braku dopasowania **oraz** pod „🔄 zmień
  kampanię" w karcie kampanii. Dopasowanie po linku buduje automatycznie, ale user może
  podmienić kampanię → propozycja **przebudowuje się od zera** dla wybranej (ten sam link+zip;
  `App.rebuildRef` trzyma `runAnalyze` z `InputPanel`, więc nie trzeba wpisywać danych ponownie)
- ✅ Propozycja na *nowej* kampanii (nazwa z UI, prefill z resztą ścieżki URL) — plan zapisu
  z `CREATE campaign` sprawdzony w dry-run; realnego zapisu jeszcze nie robiliśmy
- ✅ Formularz zlecenia: **osobne okienko na każdy adres LP + słowo klucza** przy każdym
  (wklejenie wielu linii do jednego pola rozbija je na wiersze — `<input>` po cichu zjada
  znaki nowej linii, więc `paste` jest przechwytywany)
- ✅ **Edycja nowej linii w karcie „Linie w kampanii"** (`NewLineEditor`): adres LP, nazwa LP
  i nazwa linii + „✔ Zatwierdź zmiany". Nazwa linii jest podmieniana w CAŁYM drzewie (ta sama
  semantyka co `rename_creative_all` agenta (b): przemianuj tam, gdzie linia JEST, nigdzie
  nie dokładaj), razem z `lpName`/`lpUrl` na creative i z `proposal.line` (osobna kopia
  w JSON-ie, z której orkiestrator bierze domyślne LP). Wpięte w `commit`, więc działa
  „↩ Cofnij". Dla linii REUSE'owanej edycji nie ma — odpięłaby zlecenie od istniejącej linii
- ✅ **Kilka źródeł w jednym zleceniu** (osobna sekcja niżej): własny Site per źródło,
  LP per źródło, drzewo grupowane po Site, zakładka struktury z przełącznikiem Site
- ✅ **Zakładka „🗂️ Obecna struktura kampanii"** w karcie linii (`CampaignStructure`):
  podgląd tego, co JEST w CM360 na Site zlecenia — placement → ad → creative, zwijane,
  z „🔄 odśwież" i znacznikami, które elementy dotyka bieżąca propozycja. Karmi ją ten sam
  `/api/site-structure`, z którego żyją pickery „dodaj istniejący…" (`fetchSiteStruct`
  w App), więc zakładka **nie dokłada wywołań API** poza ręcznym odświeżeniem
- ✅ Okienko uwag → `/api/refine` (AI podłączone, obie role działają na żywym Gemini)
- ✅ Repo na GitHub: **github.com/Norfeusz/CM-Worker** (prywatne)

### Dorobek sesji 04–05.08.2026 (wszystko zweryfikowane na żywo)

- ✅ **WIELE LP W JEDNYM ZLECENIU** — punkt 0 kolejki, ZROBIONY. `links[]` w API i pole na
  kilka adresów w UI; `matcher.resolve_lines` (zamyka pułapkę `max_no+1`),
  `lp_discriminators`, `match_folders_to_lps` (dopasowanie po zawieraniu **i po wspólnym
  słowie**), eskalacja `lp_material_mapping`. **Orkiestrator nie wymagał ANI JEDNEJ zmiany**
  — dowiedzione testem, nie założone.
- ✅ **Format pliku konfigurowalny per źródło** — blok `fileFormats` w `source_map.json`:
  `adSuffix` (ad `160x600_gif`), `placement` (gif→GIF, html→HTML, png→Display), `ignore`.
  Format czytany ze **słów nazwy folderu**, bo PNG i JPG dają oba `type=image`. Świadomie
  bez fallbacku na typ assetu — sufiks ma się pojawiać tylko gdy paczka sama rozdziela
  formaty. **GDN domyślnie `adSuffix`**, co nadpisuje wcześniejszą konwencję osobnych
  placementów (patrz sekcja „Decyzje… NIEZAIMPLEMENTOWANE" — ta pozycja jest już zrobiona).
- ✅ **Nowa konwencja nazw** `linia{N}-{ŹRÓDŁO}[-{słowo}]` — osobna sekcja wyżej.
- ✅ **Arkusz tagów 1:1 z CM360** — 452/452 używanych komórek ma identyczne formatowanie
  (Arial 8pt, `#99CCFF`, ramki, zawijanie, scalenia, surowe szerokości BIFF, zamrożony
  nagłówek, sekcja „Trafficking Instructions/Notes"). Nazwa:
  `Tags_{kampania}_{advertiser}_{RRRR-MM-DD}.xls`.
- ✅ **UI**: przyklejony nagłówek, jedno źródło prawdy o oczekiwaniu (`pending` + spinner
  + opis „na co czekamy"), pytania o foldery zbierane zbiorczo i **jedno** przeładowanie,
  „Wykonaj w CM360" w pasku z blokadą planu nieaktualnego, „⬇ Pobierz tagi”,
  wyszarzanie nieklikalnych przycisków, **„↩ Cofnij"** (stos 20 snapshotów wpięty w
  `commit`), identyfikacja Cube Group (Lexend, `#5172FF`, przyciski-pigułki) i ciemny motyw
  (wszystkie kolory jako tokeny; kontrast policzony, nie „na oko").
- ✅ **Launcher**: dwuklik `start.bat` stawia serwer i sam otwiera przeglądarkę
  (flaga `--open` w `serve.py` — tylko serwer wie, kiedy gniazdo jest gotowe).
- ✅ **Node naprawiony** — patrz sekcja o Node. Build React odblokowany.

### Stan repo na koniec sesji 05.08.2026
Gałąź **`feat/campaign-site-and-ai-agents`** (wypchnięta), `main` nietknięty na initial
commicie. **PR nadal nieotwarty** — `gh` nie jest zainstalowany, user otwiera klikiem:
`https://github.com/Norfeusz/CM-Worker/pull/new/feat/campaign-site-and-ai-agents`

Working tree czysty. `start.bat` (adresy webhooków + token) jest gitignorowany i **nie ma
go w historii** — sprawdzone. **Przed każdym commitem skanuj repo na `cg-pl.app.n8n.cloud`,
`sk-ant`, `AIza` oraz na wartość `N8N_TOKEN` odczytaną z `start.bat`** (nie wpisuj jej do
żadnego pliku w repo — nawet jako wzorca do wyszukiwania; ta pomyłka zdarzyła się raz i
została wycofana przed commitem). Pliki `.bat` muszą mieć **CRLF** — przy samych LF `cmd` gubi
znaki i linie `REM` wykonują się jako polecenia (kosztowało debugging).

## Foldery formatu w paczce — ZROBIONE (05.08.2026), ale konwencja się ZMIENIŁA

Wcześniej potwierdzono (30.07.2026): `GIF/`, `HTML/`, `PNG/` → **trzy osobne placementy**
(`GIF→GIF`, `HTML→HTML`, `PNG→Display`). **05.08.2026 user poprosił o coś innego** dla
realnej paczki `FRC, SPÓŁKA, KONTO - html_gif_png`: jeden placement `Display`, a format
w **nazwie ada** (`160x600_gif`). Rozstrzygnięcie: **obie konwencje są zaimplementowane
i wybieralne per źródło** blokiem `fileFormats` w `source_map.json`
(`adSuffix` | `placement` | `ignore`).

**GDN stoi domyślnie na `adSuffix`** — czyli na nowszym życzeniu, nie na starszym ustaleniu.
Przełączenie to jedno słowo w configu: `"mode": "adSuffix"` → `"placement"`.

Format czytany ze **słów nazwy folderu**, nie z typu assetu — `PNG` i `JPG` parsują się oba
jako `image`, więc z typu ich nie rozróżnisz. Alias `jpg→png` jest w configu (życzenie usera:
„przyjmij jpg jako png"). Świadomie **bez** fallbacku na typ assetu: sufiks ma się pojawiać
tylko wtedy, gdy paczka sama rozdziela formaty folderami — inaczej dopisywałby `_html` do
każdego ada zwyczajnej paczki jednoformatowej (ta pomyłka wyszła w testach).

**Nazwa placementu bierze się z FOLDERU tej porcji materiałów, nie z `format_hint` całego
zipa** (06.08.2026, zgłoszenie z paczki `META kreacje OF Biedronka.zip`: `statyki/` +
`karuzela/` dawały DWA placementy „karuzela"). Przyczyna: `karuzela` jest w
`GROUP_KEYWORDS` parsera, więc trafia do `groups`, a `statyki` nie — wpadał do resztek,
którym nazwę dawał `format_hint` **całego** zipa („Karuzela", bo słowo jest w nazwach
plików); drugi placement brał surową nazwę folderu, stąd mała litera. Teraz
`build_proposal.placement_for()` mapuje nazwę folderu przez `placementByFormat` źródła
(case-insensitive) i **to** nazywa placement. Do Facebook/Meta doszły klucze `Statyki`
i `Animacje`. Konsekwencje, o które łatwo się potknąć:
* folder będący formatem TEGO źródła nie jest już „obcą grupą": placement ma `group: null`
  i **nie ma o co pytać** — pytanie `groups` go pomija. Wcześniej pytało o niego jak o obce
  źródło, a jego domyślna odpowiedź zaznaczała tylko PIERWSZĄ grupę, więc paczka
  `karuzela/` + `posty/` po cichu gubiła drugi placement z drzewa;
* folder zużyty jako rozróżnienie LP nadal NIE dzieli placementów, nawet gdy nazywa się
  jak format (test regresyjny w `test_proposal.py`);
* nieznany folder resztek dalej dostaje nazwę z `format_hint` — inaczej każda nietypowa
  nazwa folderu robiłaby własny placement.

## Zagnieżdżone zipy: PACZKA vs JEDEN BANER (10.08.2026 — kosztowna pomyłka)

Zagnieżdżony `.zip` znaczy w dostawach dwie różne rzeczy i mieszanie ich gubi materiały:
* **jeden baner na zip** — wymiar stoi we WŁASNEJ nazwie zipa (`160x600_gdn.zip`,
  `500x400.zip`) → jedna jednostka, jak dotąd;
* **cała paczka na zip** — `mbank_…_kv1_gdn.zip` zawierający `240x400/`, `250x360/`, …
  → **jednostka na każdy wymiar w środku** (`_package_dims`).

Do 10.08.2026 parser stosował pierwszą regułę zawsze i brał z paczki **pierwszy napotkany
wymiar**. Realny skutek (zgłoszenie z żywej sesji): paczka z trzema podpaczkami
(`_gdn`/`_afiliacja`/`_programmatic`) × dwa key visuale raportowała `dimensions:
['120x600','240x400','300x250']` i `groups: []` — z 8 wymiarów GDN został jeden, a agent
AI dostał trzy wymiary z trzech RÓŻNYCH źródeł i z nich zbudował ady. **Agent nie zgadywał
— parser go wprowadził w błąd.** Wniosek na przyszłość: gdy agent oddaje złą strukturę,
najpierw sprawdź, co dostał w `ai.request.zip`.

Powiązane reguły, wszystkie z tego samego zgłoszenia:
* **Źródło czytane z nazwy PACZKI**, nie z całego zipa: `…_kv1_gdn.zip` → grupa `GDN`,
  `…_afiliacja.zip` → grupa `afiliacja` (nieznane źródło → ostatni człon nazwy, żeby KV1
  i KV3 tej samej paczki należały do JEDNEJ grupy). Bez tego materiały afiliacji wpadały
  do zlecenia GDN jako „reszta".
* **Nazwa zipa z JEDNYM banerem nie tworzy grupy** — niesie wymiar, nie źródło. Przez
  chwilę tworzyła (`gdn 1` obok folderu `GDN`, grupy o nazwach wymiarów); wyłapane na
  próbkach z `data/samples`, patrz `test_parse_zip.py`.
* **Folder `KV{N}_…` to ZESTAW materiałów**, jak `linia{N}`: jedno LP, jedna kreacja,
  rozróżnienie na adzie (`240x400_KV1`). `_set_label()` obsługuje oba wzorce. Uwaga na
  regex: po `KV1` stoi `_`, które **jest** znakiem słowa, więc `\b` tam nie trafia —
  wzorzec używa `(?![0-9])`.
* **Pytanie „które jeszcze kodujemy?" nie zaznacza już NIC domyślnie.** Wcześniejszy
  domyślny `[groups[0]]` po cichu wpuszczał pierwszą obcą grupę do zlecenia.
* **Żądanie do agenta niesie `zip.by_folder`** (`{folder: {zestaw: [wymiary]}}`) oraz
  `set_index`/`package` na jednostkach, a prompt roli (b) zakazuje brania wymiaru z innego
  folderu i każe rozwijać schemat nazw (`{wymiar}_KV#`) po realnych wymiarach — bez tego
  agent widział tylko wspólną listę wymiarów całego zipa i musiał zgadywać.

Pełny opis przypadku: `data/training_examples.md`, pozycja 3.

Nadal otwarte: `parse_zip` tworzy dla folderów HTML **równolegle** jednostki `html5` i
`image` dla tych samych wymiarów (`HTML FRC`: 15 html5 + 30 image). Dziś nieszkodliwe, bo
format bierzemy z nazwy folderu i jednostki zwijają się do tego samego ada. Zaboli, gdyby
ktoś liczył jednostki albo brał typ z reprezentanta ada.

## Kolejka — co dalej (w kolejności sugerowanego podejścia)

0. ~~**WIELE LP W JEDNYM ZLECENIU**~~ — **ZROBIONE 05.08.2026.** `links[]` w API, pole na
   kilka adresów w UI, `matcher.resolve_lines` / `lp_discriminators` /
   `match_folders_to_lps`, eskalacja `lp_material_mapping`, pytania o foldery zbierane
   zbiorczo z jednym przeładowaniem. Orkiestrator nie wymagał zmian (dowiedzione testem).
   Zweryfikowane na żywym koncie i na realnej paczce klienta (45 adów, 102 tagi).

1. **Tworzenie nowej kampanii — ZAIMPLEMENTOWANE, ale NIGDY NIE URUCHOMIONE NA ŻYWO.**
   `cm_write.campaign()` + gałąź `campaign.status=="new"` w orkiestratorze + `newCampaign`
   w `/api/build-proposal` + przycisk „➕ Nowa kampania" w UI. Domyślne: start = dziś,
   koniec = start+5 lat (`cm_write.campaign_dates`), `euPoliticalAdsDeclaration` =
   `DOES_NOT_CONTAIN_EU_POLITICAL_ADS` (nie pytamy za każdym razem).
   **Kluczowa kolejność:** CM wymaga `defaultLandingPageId` przy `campaigns.insert`, więc
   LP linii powstaje PRZED kampanią — i to samo załatwia jej rejestrację na liście stron
   docelowych (bez triku default-cycle). Kolejne LP (własne LP per creative) lecą już cyklem.
   Zweryfikowane offline (8 nowych testów w `test_orchestrate.py`) i dry-runem przez UI.
   **Do zrobienia: pierwszy realny zapis** — user świadomie go odłożył. Uwaga przy tym:
   nazwa kampanii musi być unikalna w obrębie advertisera, a przy `--execute` powstanie
   realny śmieć na koncie testowym.
2. **Realne tworzenie Site — ZROBIONE** (`/api/create-site` z `dryRun`, dwustopniowo w UI).
   Zasada: `sites.insert` na **wskazanym** wpisie Site Directory jest zwykłym zapisem, ale
   `directorySites.insert` (nowy wpis w katalogu) wymaga jawnego `allow_new_directory_site` —
   wpisy katalogu są ogólnokontowe i **nieusuwalne**. Powód, dla którego nie zgadujemy po
   nazwie: na tym koncie Site często wisi na wpisie o INNEJ nazwie (`CG_GDN` →
   `CG_remarketing`, `Gmail` → `CG Gmail`, `YouTube` → `Google - YouTube`), więc dopasowanie
   po nazwie robiłoby duplikaty. `/api/commit` przy realnym zapisie **odmawia z góry**, gdy
   Site nie istnieje — inaczej awaria na kroku 2 zostawiłaby zapisane LP i kampanię.
3. **Wpięcie `ai_fallback.escalations()` do `/api/build-proposal` — ZROBIONE.**
   `serve._attach_ai()` dokłada do propozycji `ai.escalations`, `ai.request` (gotowy kontrakt
   dla roli (a), żeby `/api/assist` nie musiało ponownie parsować zipa) i `ai.wired`.
   UI pokazuje panel „Punkty niskiej pewności”.
4. **Agenci AI przez n8n — ZROBIONE (rola a i b), oprócz promocji decyzji do configu.**
   Patrz sekcja „Agenci AI” niżej. **POZOSTAJE krytyczny wymóg z pamięci**: zatwierdzone
   decyzje AI muszą wracać do configu (`source_map.json`, `advertiser_map.json`), żeby
   analogiczny przypadek nie wymagał AI drugi raz. Zaprojektowane jako `scripts/promote.py`
   + `POST /api/promote` (diff → zatwierdzenie → zapis, z proweniencją `_source: "ai"`),
   **jeszcze nie napisane** — to następny krok i bez niego AI jest kosztem stałym, nie
   jednorazowym.
5. **n8n — POSTAWIONE po stronie użytkownika** (serwer firmowy). Workflow do importu i
   instrukcja: `n8n/`. `docs/n8n-ai-architecture.md` jest w tym punkcie **nieaktualny** —
   przewidywał `Front → n8n → Python`, a realnie jest odwrotnie (patrz niżej).
6. **Trening na kolejnych parach (zip+wiadomość → docelowa struktura)** — user dostarcza je
   iteracyjnie, log w `data/training_examples.md`.
7. **Programmatic jako przypadek szczególny** — świadomie odłożone od początku projektu.
   Różni się tym, że tam realnie wgrywamy assety (nie tylko szablon 1×1). Od 06.08.2026
   programmatic **da się już trafficować obok GDN jako zwykłe źródło trackingowe**
   (patrz „Kilka źródeł"), ale upload assetów wciąż nie istnieje.
8. Drobne: obsługa `.7z` bezpośrednio w `parse_zip.py` (dziś tylko `.zip`, choć `py7zr` jest
   zainstalowane i sprawdzone ręcznie), sprzątanie artefaktów testowych w CM360 (nieszkodliwe,
   user powiedział że narazie nie trzeba).
9. **Build React — ODBLOKOWANY** (Node 24 + npm 11 działają, patrz sekcja o Node wyżej).
   Nie jest to migracja: UI już jest React 18, chodzi o dodanie builda i rozbicie
   `ui/index.html` (**1500+ linii**) na komponenty. Niższy priorytet niż użyteczność, ale
   plik jest już na granicy wygodnej pracy — to najmocniejszy argument, żeby to zrobić.
10. ~~Uwagi do wyglądu i użyteczności UI~~ — **ZROBIONE 05.08.2026** (sticky header,
   wskaźnik pracy, cofanie, styl Cube Group, ciemny motyw, pobieranie tagów, wyszarzanie).
11. Oznaczać, **po którym LP nastąpiło automatyczne dopasowanie kampanii** (user zgłosił
   04.08.2026, świadomie odłożone na potem). **NADAL NIEROBIONE.**
12. ~~**Etykieta z `utm_campaign` bywa za długa.**~~ — **ZROBIONE 06.08.2026** przez słowo
    klucza per adres (patrz sekcja o konwencji nazw). Automatyczne skracanie do członu przed
    cyframi świadomie NIE zostało zrobione: user podaje wprost, czego chce.
13. **Cofanie nie ma „ponów" (redo)** ani skrótu Ctrl+Z. Świadomie minimalny zakres.
14. **Nazwy adów w paczkach Meta niosą nazwę folderu** (`statyki_1080x1920_1`), która po
   06.08.2026 stoi już w nazwie placementu (`Statyki`) — czyli dubluje się. Zostawione
   bez zmian: user zgłaszał tylko placementy, a `adKey: variant_dim_card` dotyczy też
   karuzeli, gdzie bez prefiksu zostałoby samo `1`/`2`/`3`. **Do decyzji usera.**

## HANDOFF — pierwsze kroki w nowej sesji

1. `py tests/test_matcher.py` … i pozostałe sześć plików. **Musi być 368/368.** Jeśli nie —
   zatrzymaj się i zdiagnozuj, zanim cokolwiek dopiszesz.
2. Serwer: poproś usera o **dwuklik `start.bat`** (stawia serwer i otwiera przeglądarkę).
   **Nie stawiaj `serve.py` jako swojego zadania w tle na stałe** — jego czas życia jest
   powiązany z sesją agenta, padł już wielokrotnie. Własny proces tylko na czas konkretnej
   weryfikacji, i pamiętaj, że **restart jest konieczny po każdej zmianie w Pythonie**
   (moduły siedzą w pamięci procesu).
3. Gałąź `feat/campaign-site-and-ai-agents`, PR nieotwarty. Working tree powinien być czysty.
4. **Zmiany w promptach agentów wymagają jednego przebiegu na ŻYWYM modelu.** Dwie takie
   zmiany czekają na weryfikację i to jest najbliższy priorytet techniczny:
   - nowa konwencja nazw `linia{N}-{ŹRÓDŁO}[-{słowo}]` w prompcie roli (b),
   - nowa operacja `rename_creative_all` (model musi ją WYBIERAĆ zamiast
     `apply_creative_to_all` przy zmianie nazwy linii).
   Atrapa webhooka w testach tego nie wyłapie — jej odpowiedzi pisze się pod własne
   założenia. Blokada w `apply_ops` chroni niezależnie od tego, ale sam wybór operacji
   potwierdzi tylko żywy test.
5. Największa zaległość merytoryczna to wciąż **`promote.py`** (punkt 4) — bez niego AI jest
   kosztem stałym, nie jednorazowym.

## Styl pracy z tym użytkownikiem (Norbert)

- Domyślnie pracuj na **Sonnet** — użytkownik świadomie zszedł z Opusa przy dużym projekcie
  (koszt tokenów). Jeśli trafisz na coś genuinie złożonego (subtelna architektura, trudny
  debugging API), **zapytaj wprost**, czy przełączyć na Opusa — nie rób tego po cichu.
- Duże zmiany rób **krok po kroku** z jawnym planem (user to preferuje), z testami po każdym kroku.
- Przy realnych zapisach do CM360: **zawsze dry-run najpierw**, pytaj przed włączeniem zapisu.
- Weryfikuj zmiany UI **na żywo w przeglądarce** (Browser tool), nie tylko czytaniem kodu —
  ten projekt ma historię subtelnych bugów UI wyłapanych dopiero live (np. konflikt nazw zmiennych
  `commit`, przemianowanie sąsiedniego creative przez dopasowanie po samej nazwie).
- Pliki dialogu wyboru pliku (`<input type=file>`) nie są sterowalne przez browser tool — do
  testów wstrzykuj plik przez JS (`DataTransfer` + `dispatchEvent('change')`), pamiętając że
  na stronie mogą być DWA `<input type=file>` (ukryty „Wczytaj JSON" w nagłówku + właściwy
  w formularzu) — zawsze sprawdź który jest który przed wypełnieniem.
