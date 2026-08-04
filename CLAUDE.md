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

**Z warstwą AI: `start.bat`** (gitignorowany, user ma go u siebie; szablon to
`start.example.bat`). Ustawia `N8N_STRUCTURE_URL`, `N8N_INTENT_URL`, `N8N_TOKEN` i startuje
serwer w jednym — bo `set` obowiązuje tylko w bieżącym oknie, a `serve.py` musi wystartować
w tym samym. **Zmienne czyta na starcie**, więc po edycji promptów w `ai_agents.py`
konieczny jest restart (moduł siedzi w pamięci procesu; objawia się identyczną odpowiedzią
po poprawce).

**Nie stawiaj `serve.py` jako swojego zadania w tle** — trzy razy padł, bo jego czas życia
jest powiązany z zadaniami agenta, nie z sesją użytkownika. Poproś użytkownika o dwuklik na
`start.bat`; własny proces stawiaj tylko na czas konkretnej weryfikacji.
Testy offline: `py tests/test_matcher.py`, `test_proposal.py`, `test_orchestrate.py`,
`test_create_site.py`, `test_ai_agents.py` (131/131 zielone na dzień pisania tego pliku —
uruchom je jako pierwszy krok, żeby potwierdzić, że nic się nie popsuło od ostatniej sesji).
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
```

### Endpointy `scripts/serve.py` (GET serwuje też statyczne pliki z `ui/`)
- `POST /api/build-proposal` — `{link, source, message, zipB64|zipPath, campaignId?, newCampaign?}` → kontrakt propozycji
  (`campaignId` to override, gdy user ręcznie wybrał kampanię z przeglądarki zamiast auto-dopasowania;
  `newCampaign` to nazwa kampanii do utworzenia — propozycja wraca z `campaign.status="new"`, `id=null`)
- `POST /api/refine` — `{proposal, answers, remarks}` → **Agent (b)**: uwagi → operacje z n8n →
  `ai_agents.apply_ops` stosuje je deterministycznie → `{proposal, log, applied, skipped, unclear}`
- `POST /api/assist` — `{proposal}` → **Agent (a)**: podpowiedzi do struktury (`ai.request`
  z propozycji leci do n8n). Zwraca **tylko sugestie**, drzewa nie rusza
- `POST /api/commit` — `{proposal, dryRun}` → uruchamia orkiestrator (dry-run albo realny zapis + eksport tagów)
- `GET /api/sites?q=` — kaskada wyszukiwania site (konto → Site Directory), jak natywny dialog CM
- `POST /api/create-site` — plan (dry-run) dodania nowego site
- `GET /api/site-structure?campaignId=&site=` — istniejące placementy/ady/creative dla pickerów w UI
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
   gdzie ma być źródło. Poprawne: LP = `linia{N}-{ŹRÓDŁO}` (a przy wielu LP na linię
   `linia{N}-{wariant}-{ŹRÓDŁO}`), creative = `linia{N}-{audience}`. Potwierdzone żywymi
   danymi konta (`linia1-FB`, `linia2-GDN`).
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
- ✅ Okienko uwag → `/api/refine` (seam gotowy, AI jeszcze nie podłączone)
- ✅ Repo na GitHub: **github.com/Norfeusz/CM-Worker** (prywatne)

### Stan repo na koniec sesji 30.07.2026
Cała praca tej sesji siedzi na gałęzi **`feat/campaign-site-and-ai-agents`** (wypchniętej),
`main` jest nietknięty i stoi na initial commicie. **PR nie został jeszcze otwarty** —
`gh` nie jest zainstalowany, więc user robi to kliknięciem:
`https://github.com/Norfeusz/CM-Worker/pull/new/feat/campaign-site-and-ai-agents`

Commity (po granicach plików, bez rozcinania hunków): nowa kampania + Site → agenci AI →
API i UI → dokumentacja → 4 poprawki z żywych testów. Working tree czysty.
`start.bat` (adresy webhooków + token) jest gitignorowany i **nie ma go w historii** —
sprawdzone. Przed każdym commitem skanuj repo na `cg-pl.app.n8n.cloud`, `sk-ant`, `AIza`.

## Decyzje domenowe potwierdzone przez użytkownika, ale JESZCZE NIEZAIMPLEMENTOWANE

**Foldery formatu w paczce GDN → osobne placementy.** Paczka podzielona na `GIF/`, `HTML/`,
`PNG/` (każdy z podfolderami wymiarów) to **trzy różne placementy**, a mapowanie
`GIF→GIF`, `HTML→HTML`, `PNG→Display` jest **ogólną regułą dla GDN** (potwierdzone
30.07.2026). Każdy placement dostaje dokładnie te wymiary, które leżą w jego folderze.

Dziś rdzeń deterministyczny tego NIE robi: `parse_zip._detect_groups` rozpoznaje foldery
tylko z zamkniętej listy `GROUP_KEYWORDS` (`gdn`, `screening`, `facebook`, `karuzela`…), a
`gif`/`html`/`png` tam nie ma — więc lądują jako **warianty**, a dla GDN `adKey="dimension"`
ignoruje warianty, czyli 9 jednostek (3 wymiary × 3 formaty) zwija się do 3 adów i
rozróżnienie formatów przepada w parserze. Rozbicie trzeba dziś zrobić uwagami do roli (b)
(działa, sprawdzone na żywym Gemini: 14 operacji, 0 pominiętych).

**Użytkownik świadomie wstrzymał implementację** — chce najpierw potwierdzić na realnych
paczkach, że agent radzi sobie z tym powtarzalnie. Docelowo to **wzorcowy przypadek dla
`promote.py`**: zatwierdzona decyzja AI trafia do `source_map.json` (mapowanie
folder→placement dla GDN) + rozszerzenie `GROUP_KEYWORDS`, i analogiczna paczka nie wymaga
już modelu.

## Kolejka — co dalej (w kolejności sugerowanego podejścia)

0. **WIELE LP W JEDNYM ZLECENIU — najnowsze zadanie od użytkownika (30.07.2026), nietknięte.**

   Dziś jedno zlecenie = **jeden link** = jedna linia (`/api/build-proposal` bierze `link`
   jako string, `matcher.resolve_line` liczy jedną linię). Docelowo user chce wkleić **kilka
   LP naraz** — wszystkie trafiają do **tej samej kampanii** — a narzędzie ma **samo
   spróbować przypisać materiały do LP**, analizując link i nazwy folderów w zipie.

   Przykład, o którym mówimy: zip z folderami `prospecting/` i `remarketing/` (albo
   `slonce/`, `niebo/`) + dwa LP różniące się `utm_medium=prospecting` /
   `utm_medium=remarketing`. Materiały z folderu mają wylądować na linii tego LP, którego
   URL pasuje do nazwy folderu.

   **Co już jest gotowe i nie trzeba tego pisać od nowa:**
   - **Kształt wyjściowy istnieje i jest przetestowany.** Jeden Ad może nieść wiele creative,
     a każdy creative może mieć **własny LP** (`creative.lpName` / `creative.lpUrl`).
     Orkiestrator to obsługuje (`Orchestrator._lp_key`), tworzy każdy distinct LP i
     rejestruje go w kampanii — pokryte testami w `test_orchestrate.py` (przypadek
     `linia3-slonce` / `linia3-niebo` z osobnymi LP). **Ścieżki zapisu NIE trzeba ruszać.**
   - **Rola (a) już wyciąga wiele linii z wiadomości** — `STRUCTURE_SCHEMA.lines` zwraca
     listę `{lpUrl, source, audience, lpName, creativeName}` i na żywym Gemini działa
     poprawnie (zweryfikowane: dwa LP prospecting/remarketing → dwie linie, prawidłowe
     nazwy). Czyli inteligencja do interpretacji LP w dużej części istnieje.
   - Konwencja nazw: LP = `linia{N}-{ŹRÓDŁO}`, a przy wielu LP na linię
     `linia{N}-{wariant}-{ŹRÓDŁO}`; creative = `linia{N}-{audience}`.

   **Co trzeba dopisać:**
   1. **Wejście**: `/api/build-proposal` musi przyjąć listę linków (np. `links: [...]`,
      zachowując `link` dla zgodności) + pole w UI na kilka adresów (jeden na linię).
   2. **Dopasowanie deterministyczne folder ↔ LP** — to sedno zadania i to ma być
      rdzeń, nie AI. Sygnały: człony ścieżki i parametry query LP (`utm_medium`,
      `utm_content`, `sprzedawca`) kontra nazwa folderu w zipie (`parse_zip` zwraca
      `variant` = folder najwyższego poziomu i `group`). Normalizacja: lowercase, bez
      polskich znaków, dopasowanie po zawieraniu w obie strony.
   3. **Numeracja linii dla kilku LP naraz** — `matcher.resolve_line` woła się per LP na
      **tej samej** liście LP kampanii; uwaga: dwa nowe LP w jednym zleceniu nie mogą
      dostać tego samego numeru, a `resolve_line` liczy `max_no + 1` z *istniejących* LP,
      więc trzeba dokładać już przydzielone w tej sesji. To realna pułapka.
      `detect_line_conflict` też trzeba puścić per LP.
   4. **Eskalacja przy niejednoznaczności** — gdy folderu nie da się przypisać (albo
      pasuje do kilku LP), nowy kod eskalacji w `ai_fallback.escalations()` (np.
      `lp_material_mapping`) i pytanie sterujące w UI. Dopasowania zatwierdzone przez
      użytkownika są **kandydatem do promocji** przez `promote.py` (punkt 4 niżej).

   **Uwaga na kolejność prac:** to zadanie i `promote.py` mocno się zazębiają. Sensowniej
   zrobić najpierw dopasowanie deterministyczne + eskalację, a promocję dopiąć jako jeden
   mechanizm dla wszystkich decyzji AI, niż budować promocję dwa razy.

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
   Różni się tym, że tam realnie wgrywamy assety (nie tylko szablon 1×1).
8. Drobne: obsługa `.7z` bezpośrednio w `parse_zip.py` (dziś tylko `.zip`, choć `py7zr` jest
   zainstalowane i sprawdzone ręcznie), sprzątanie artefaktów testowych w CM360 (nieszkodliwe,
   user powiedział że narazie nie trzeba).
9. **Build React — ODBLOKOWANY** (Node 24 + npm 11 działają, patrz sekcja o Node wyżej).
   Nie jest to migracja: UI już jest React 18, chodzi o dodanie builda i rozbicie
   `ui/index.html` na komponenty. **Niższy priorytet niż uwagi do wyglądu/użyteczności** —
   te przenoszą się do builda bez zmian, więc nie ma sensu na niego czekać.
10. **Uwagi użytkownika do wyglądu i użyteczności UI** (zgłoszone 04.08.2026, nierobione):
   górny pasek przyklejony do krawędzi ekranu (sticky header), wyraźniejsza informacja że
   coś się przetwarza. User miał podać pełną listę — dopytaj o resztę przed startem.
11. Oznaczać, **po którym LP nastąpiło automatyczne dopasowanie kampanii** (user zgłosił
   04.08.2026, świadomie odłożone na potem).

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
