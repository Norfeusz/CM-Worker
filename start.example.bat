@echo off
REM ===================================================================
REM  CM Worker — uruchomienie z warstwa AI
REM
REM  JAK UZYWAC:
REM    1. Skopiuj ten plik jako  start.bat  (w tym samym katalogu)
REM    2. Wpisz nizej swoje wartosci
REM    3. Klikaj dwukrotnie start.bat
REM
REM  start.bat jest w .gitignore, wiec Twoje adresy i token NIE wejda
REM  do repozytorium. Tego pliku (start.example.bat) nie edytuj.
REM ===================================================================

REM --- Adresy webhookow z n8n -----------------------------------------
REM Wez PRODUCTION URL z wezla "Webhook" (ten z /webhook/, nie /webhook-test/).
REM Workflow musi byc Active, inaczej dostaniesz 404.
set N8N_STRUCTURE_URL=https://n8n.twojafirma.pl/webhook/cm-worker-structure
set N8N_INTENT_URL=https://n8n.twojafirma.pl/webhook/cm-worker-intent

REM --- Wspolny sekret --------------------------------------------------
REM DOKLADNIE ta sama wartosc, co CM_TOKEN w wezle "Zbuduj zadanie"
REM w OBU workflow. Wymysl dlugi losowy ciag.
set N8N_TOKEN=zmien-to-na-dlugi-losowy-ciag

REM --- Opcjonalnie -----------------------------------------------------
REM Limit czasu na odpowiedz modelu (sekundy, domyslnie 120)
REM set N8N_TIMEOUT=180

REM ===================================================================
cd /d "%~dp0"
REM Konsola Windows domyslnie nie jest UTF-8, wiec polskie znaki w komunikatach
REM serwera wychodzily jako "zatrzymaA‡". 65001 = UTF-8.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo Startuje CM Worker...
echo   struktura: %N8N_STRUCTURE_URL%
echo   uwagi:     %N8N_INTENT_URL%
echo.
REM --open sprawia, ze przegladarka otwiera sie SAMA, gdy serwer zacznie nasluchiwac.
REM Robi to serve.py, a nie ten plik: tylko serwer wie, kiedy gniazdo jest gotowe,
REM a "start http://..." z opoznieniem bylby wyscigiem (przy zimnym starcie import
REM bibliotek Google trwa dluzej i przegladarka pokazywala blad polaczenia).
REM
REM Zamkniecie TEGO okna zatrzymuje serwer.
REM -u wylacza buforowanie, zeby komunikaty pojawialy sie od razu, a nie na koncu.
py -u scripts\serve.py --open
pause
