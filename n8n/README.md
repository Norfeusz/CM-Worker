# n8n — warstwa AI dla CM Worker

## Kierunek ruchu (ważne)

```
[UI w przeglądarce] → [serve.py na laptopie] → (HTTPS, wychodząco) → [n8n na serwerze] → [Claude]
                            │
                            └→ [CM360]   ← zapisy TYLKO tędy, przez bezpiecznik cm_auth
```

`serve.py` **woła n8n**, nie odwrotnie. Dwa powody:

1. n8n na serwerze firmowym nie dosięgnie `127.0.0.1:8765` na laptopie traffickera.
2. Bezpiecznik (`cm_auth`: allowlist profilu/advertisera, DELETE nigdy) zostaje w Pythonie
   i n8n go nie omija. n8n nie ma i nie potrzebuje żadnego dostępu do CM360.

## Co jest gdzie (podział odpowiedzialności)

| Element | Gdzie żyje | Dlaczego tam |
|---|---|---|
| Prompt systemowy, schemat wyjścia | repo (`scripts/ai_agents.py`) | wersjonowane w gicie, review w PR, jedno źródło prawdy |
| Klucz API | n8n (credential) | **nigdy nie ląduje na laptopie traffickera** |
| **Wybór dostawcy** (Gemini/Anthropic) | n8n (który workflow zaimportowany) | kod jest agnostyczny — patrz niżej |
| Model i parametry generowania | n8n (węzeł „Zbuduj żądanie”) | zmiana modelu bez ruszania kodu i bez deployu |
| Walidacja odpowiedzi | repo (`ai_agents.validate`) | odpowiedź niezgodna ze schematem jest odrzucana u nas |
| Zastosowanie zmian do struktury | repo (`ai_agents.apply_ops`) | deterministycznie; model proponuje, kod decyduje |

Workflow jest więc **cienkim przekaźnikiem**. To celowe: cała wartość n8n w tym miejscu to
custody klucza + centralne logi wykonań, a nie orkiestracja.

## Wybór dostawcy modelu

Dwa gotowe workflow, **identyczny kontrakt webhooka** — Python nie widzi różnicy:

| Plik | Dostawca | Credential (Header Auth) | Domyślny model |
|---|---|---|---|
| `cm-worker-agent-gemini.json` | Gemini API | nazwa `x-goog-api-key` | `gemini-3.5-flash` |
| `cm-worker-agent.json` | Anthropic | nazwa `x-api-key` | `claude-opus-5` |

Oba wymuszają schemat po stronie API (structured outputs), więc model **nie może** zwrócić
innego kształtu. Gemini przyjmuje standardowy JSON Schema, więc schematy z repo lecą do
obu dostawców bez żadnego tłumaczenia.

Zmiana dostawcy później = podmiana workflow w n8n. Zero zmian w kodzie, zero restartu
narzędzia poza ewentualną zmianą URL-i webhooków.

**Dlaczego HTTP Request, a nie natywny węzeł „Google Gemini Chat Model" / AI Agent?**
Bo natywne węzły nie wystawiają `response_format.schema` (ani `output_config.format`).
Straciliśmy przez to wymuszenie schematu **po stronie API** — jedyną gwarancję, że model
fizycznie nie może zwrócić innego kształtu. Zostałaby wyłącznie walidacja w Pythonie, która
wtedy odrzuca złe odpowiedzi, zamiast im zapobiegać. Nie zmieniaj tego na natywny węzeł
„dla uproszczenia" bez świadomej decyzji, że rezygnujesz z tej gwarancji.

## Instalacja (jednorazowo, po Twojej stronie)

1. **Credential.** Dwie drogi — sprawdź pierwszą, bo nie wymaga wklejania klucza dwa razy:

   **(A) Wbudowany typ n8n** *(Gemini; zalecane, jeśli Twoja wersja n8n to oferuje)*
   Masz już (albo tworzysz) credential **„Google Gemini(PaLM) Api"** — Host
   `https://generativelanguage.googleapis.com`, klucz z `aistudio.google.com`.
   W węźle *Gemini (Interactions API)* ustaw **Authentication → Predefined Credential
   Type** i wyszukaj „Gemini". Jeśli jest na liście — wybierz i gotowe.

   > Dokumentacja n8n wymienia ten credential tylko przy czterech natywnych węzłach
   > (Google Gemini, Gemini Chat Model, Embeddings ×2) i **nie potwierdza** dostępności
   > w HTTP Request. Zależy to od wersji — dlatego plik ma domyślnie wariant (B).

   > ⚠️ **Przycisk *Test* na tym credentialu może pokazać błąd przy poprawnym kluczu** —
   > znany bug n8n (wysyła POST tam, gdzie Gemini chce GET). Nie diagnozuj po nim;
   > testuj uruchomieniem workflow.

   **(B) Header Auth** *(działa zawsze, tak jest w pliku)*
   *Credentials → New → Header Auth*, nazwa i wartość według tabeli powyżej.
   - Gemini: `x-goog-api-key` + klucz z `aistudio.google.com`
   - Anthropic: `x-api-key` + klucz z `platform.claude.com`

   Klucza nie wpisuję i nie chcę go widzieć — w obu wariantach wklejasz go sam.
   Chcecie iść przez **Vertex AI** (istniejące rozliczenie Google Cloud)? Zmienia się URL
   i uwierzytelnianie na service account — powiedz, dostosuję węzeł.
2. **Import.** *Workflows → Import from File* → plik z tabeli.
   W węźle wywołującym model wybierz credential z punktu 1.
3. **Token współdzielony** (zalecane). W węźle „Zbuduj żądanie” ustaw `CM_TOKEN` na losowy
   ciąg. Bez tego każdy, kto pozna URL webhooka, wydaje Wasz budżet API.
4. **Dwie instancje.** Ten sam plik importujesz **dwa razy**; różni je tylko ścieżka
   webhooka — prompt i schemat przychodzą w payloadzie, więc reszta konfiguracji jest
   identyczna.

   Ścieżkę ustawia się w **pierwszym węźle („Webhook") → pole `Path`**. Domyślnie jest
   tam `cm-worker-agent`; zmień na:
   - **`cm-worker-structure`** → rola (a), budowanie struktury
   - **`cm-worker-intent`** → rola (b), interpretacja uwag

   Kolejność ma znaczenie: **import → zmień `Path` → Save → Activate.** Dwa aktywne
   workflow nie mogą mieć tej samej ścieżki, więc aktywacja drugiego przed zmianą
   ścieżki skończy się konfliktem.

   *Mniej klikania:* zrób dwie kopie pliku i podmień w nich `"path"` (oraz `"name"`)
   przed importem.
5. **Aktywuj** oba workflow (przełącznik *Active*), a potem skopiuj z węzła Webhook
   **Production URL** — nie Test URL.

   | | Adres | Kiedy działa |
   |---|---|---|
   | Test URL | `…/webhook-test/<path>` | tylko po kliknięciu *Listen for test event* |
   | **Production URL** | `…/webhook/<path>` | tylko gdy workflow jest **Active** |

   Do zmiennych środowiskowych idzie **Production URL**. `404` z narzędzia to prawie
   zawsze nieaktywny workflow albo wklejony Test URL.

## Konfiguracja narzędzia (na laptopie)

Trzy zmienne środowiskowe. **Zalecane: plik uruchomieniowy** — ustawia je i startuje serwer
w jednym, więc nie da się ich rozjechać:

```bash
copy start.example.bat start.bat
```

Wpisujesz wartości w `start.bat` i klikasz go dwukrotnie. Jest w `.gitignore`, więc Twoje
adresy i token nie wejdą do repo.

| Zmienna | Wartość |
|---|---|
| `N8N_STRUCTURE_URL` | **Production URL** workflow roli (a) |
| `N8N_INTENT_URL` | **Production URL** workflow roli (b) |
| `N8N_TOKEN` | dokładnie to samo, co `CM_TOKEN` w węźle „Zbuduj żądanie” w **obu** workflow |

> ⚠️ **`set` obowiązuje tylko w bieżącym oknie terminala.** Ustawienie zmiennych w jednym
> oknie i uruchomienie `serve.py` w drugim nie zadziała — to najczęstsza przyczyna „ustawiłem,
> a i tak pisze, że nie podpięte”. Na stałe: `setx` (działa dopiero w NOWYCH oknach) albo
> *Właściwości systemu → Zmienne środowiskowe*. W PowerShellu składnia to
> `$env:N8N_TOKEN = "..."`, nie `set`.

**Weryfikacja:** zbuduj propozycję i sprawdź przyciski AI — albo *Podgląd JSON* → `ai.wired`
powinno pokazać `{"structure": true, "intent": true}`.

Bez tych zmiennych narzędzie działa normalnie — funkcje AI mówią tylko, że nie są podpięte.

## Kontrakt webhooka

**Żądanie** (wysyła `serve.py`):
```json
{ "system": "<prompt systemowy>", "schema": { "...": "JSON Schema" }, "input": { "...": "dane" } }
```

**Odpowiedź**: sam obiekt JSON agenta. Owijka `[{...}]` albo `{"output": {...}}` jest
rozpakowywana automatycznie. Odpowiedź niezgodna ze `schema` jest odrzucana po stronie
Pythona z wypisaniem konkretnych niezgodności.

Schemat jest wymuszany przez `output_config.format` (structured outputs) — model
**fizycznie nie może** zwrócić innego kształtu, więc walidacja u nas to druga linia obrony,
nie pierwsza.

## Diagnostyka

| Objaw | Przyczyna |
|---|---|
| `Brak N8N_*_URL w środowisku` | zmienna nieustawiona albo `serve.py` startował przed jej ustawieniem |
| `n8n odpowiedziało 404` | workflow nieaktywny (URL testowy działa tylko po kliknięciu *Execute*) |
| `Brak lub zły X-CM-Token` | `CM_TOKEN` w n8n ≠ `N8N_TOKEN` w środowisku |
| `Odpowiedź agenta nie pasuje do schematu` | zwykle inny model bez structured outputs — sprawdź `MODEL` w węźle „Zbuduj żądanie” |
| `Odpowiedź ucięta na max_tokens` (Anthropic) | podnieś `MAX_TOKENS` w węźle „Zbuduj żądanie” |
| `output_text nie jest poprawnym JSON` (Gemini) | odpowiedź ucięta — zwykle za duży payload; ten interfejs nie zwraca flagi ucięcia, więc rozpoznajemy to po błędzie parsowania |
| `Brak output_text w odpowiedzi Gemini` | model nie wyprodukował odpowiedzi (filtry bezpieczeństwa albo błędny `MODEL`) — pełne body jest w komunikacie błędu i w logu wykonania n8n |
