@echo off
REM ===================================================================
REM  CM Worker — PRODUKCJA (konto MBank, profil 9765911)
REM
REM  UWAGA: to konto KLIENTA. Uruchamiaj to dopiero wtedy, gdy ten sam
REM  material przeszedl poprawnie przez wersje TESTOWA (start.bat).
REM
REM  JAK UZYWAC:
REM    1. Skopiuj ten plik jako  start-prod.bat  (w tym samym katalogu)
REM    2. Wpisz nizej te same wartosci co w start.bat
REM    3. Klikaj dwukrotnie start-prod.bat
REM
REM  start-prod.bat jest w .gitignore, wiec Twoje adresy i token NIE wejda
REM  do repozytorium. Tego pliku (start-prod.example.bat) nie edytuj.
REM ===================================================================

REM --- WYBOR KONTA -----------------------------------------------------
REM To JEDYNA roznica wobec start.bat. Bez tej zmiennej narzedzie pracuje
REM na koncie testowym (Cube Group). Bezpiecznik w cm_auth.py czyta ja
REM i od tego momentu konto testowe jest niedostepne, a produkcyjne — tak.
set CM_ENV=prod

REM --- ZAPISY NA PRODUKCJI ---------------------------------------------
REM DOMYSLNIE WYLACZONE. Bez tej zmiennej narzedzie tylko CZYTA konto
REM klienta: mozna przegladac kampanie, budowac propozycje i ogladac
REM dry-run, ale zaden zapis nie wyjdzie do CM360.
REM
REM Odkomentuj DOPIERO gdy swiadomie chcesz zapisywac na koncie klienta.
REM set CM_PROD_WRITES=1

REM --- Adresy webhookow z n8n -----------------------------------------
set N8N_STRUCTURE_URL=https://n8n.twojafirma.pl/webhook/cm-worker-structure
set N8N_INTENT_URL=https://n8n.twojafirma.pl/webhook/cm-worker-intent

REM --- Wspolny sekret --------------------------------------------------
set N8N_TOKEN=zmien-to-na-dlugi-losowy-ciag

REM ===================================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo.
echo  ############################################################
echo  #                                                          #
echo  #   UWAGA: PRODUKCJA — konto klienta (MBank, 9765911)      #
echo  #                                                          #
echo  ############################################################
echo.
if defined CM_PROD_WRITES (
  echo   ZAPISY: WLACZONE — kazdy commit trafi na konto klienta.
) else (
  echo   ZAPISY: wylaczone ^(tylko odczyt^). Zeby wlaczyc, odkomentuj
  echo           CM_PROD_WRITES=1 w tym pliku.
)
echo.
py -u scripts\serve.py --open
pause
