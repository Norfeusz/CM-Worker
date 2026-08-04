<#
.SYNOPSIS
  Ustawia Node/npm dla BIEŻĄCEJ sesji PowerShell. Nic nie instaluje i nie zmienia
  trwale środowiska — po zamknięciu okna wraca stan poprzedni.

.DESCRIPTION
  Dlaczego to jest potrzebne, a nie wystarczy zwykły PATH:

  Na tej maszynie nvm-for-windows zostało zainstalowane na koncie `admin1`.
  W SYSTEMOWYM PATH siedzą dwa wpisy wskazujące do profilu tego użytkownika:

      C:\Users\admin1\AppData\Local\nvm
      C:\nvm4w\nodejs        (symlink -> C:\Users\admin1\AppData\Local\nvm\v14.21.3)

  Windows przetwarza PATH systemowy PRZED PATH użytkownika, więc `npm` zawsze trafia
  najpierw na `C:\nvm4w\nodejs\npm.cmd`. Ten npm próbuje rozwiązać ścieżkę swojego
  modułu przez symlink i robi `lstat` na `C:\Users\admin1` — czego zwykłe konto nie
  może. Efekt to `EPERM: operation not permitted, lstat 'C:\Users\admin1'`.

  Wniosek, który warto znać: npm NIE jest uszkodzony. Jest ZASŁONIĘTY przez martwy
  symlink w PATH systemowym. Rozpakowany Node w profilu użytkownika działa bez
  zarzutu, o ile wywoła się go pierwszy.

  Systemowego PATH nie da się poprawić bez uprawnień administratora (ich tu nie ma),
  ani nadpisać wpisem użytkownika (bo jest później w kolejności). Dlatego kolejność
  wymusza się na poziomie SESJI — i to robi ten skrypt.

  Właściwa naprawa docelowa: poprosić IT o usunięcie obu martwych wpisów `admin1`
  z systemowego PATH. Wtedy ten skrypt przestanie być potrzebny.

.PARAMETER Version
  Numer wersji lub jej początek ("24", "14", "24.19"). Bez parametru bierze
  najwyższą znalezioną. Wersje wykrywane są z katalogów `nodejs-*` w profilu
  użytkownika, więc skrypt działa też u kogoś innego, kto rozpakował je tak samo.

.EXAMPLE
  . .\node-env.ps1
  Najnowszy dostępny Node w tej sesji.

.EXAMPLE
  . .\node-env.ps1 -Version 14
  Powrót do Node 14 (np. gdy coś działa tylko na starej wersji).

.NOTES
  Uruchamiaj z KROPKĄ na początku (`. .\node-env.ps1`) — inaczej zmiana PATH zniknie
  razem z podprocesem skryptu i nic nie zadziała.
#>
param([string]$Version = "")

$ErrorActionPreference = "Stop"

function Find-NodeInstalls {
    # Katalogi `nodejs-*` w profilu; node.exe leży albo wprost w nich, albo o jeden
    # poziom głębiej (tak rozpakowuje się oficjalny ZIP: nodejs-24\node-v24...-win-x64).
    $found = @()
    Get-ChildItem "$env:USERPROFILE\nodejs-*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object {
            $candidates = @($_) + @(Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue)
            foreach ($c in $candidates) {
                $exe = Join-Path $c.FullName "node.exe"
                if (Test-Path $exe) {
                    $v = (& $exe --version 2>$null)
                    if ($v) { $found += [pscustomobject]@{ Version = $v.TrimStart('v'); Path = $c.FullName } }
                    break
                }
            }
        }
    $found | Sort-Object { [version]($_.Version) } -Descending
}

$installs = Find-NodeInstalls
if (-not $installs) {
    Write-Host "Nie znalazłem żadnego Node w $env:USERPROFILE\nodejs-*" -ForegroundColor Red
    Write-Host "Rozpakuj oficjalny ZIP z nodejs.org do np. $env:USERPROFILE\nodejs-24" -ForegroundColor Yellow
    return
}

$pick = if ($Version) { $installs | Where-Object { $_.Version.StartsWith($Version.TrimStart('v')) } | Select-Object -First 1 }
        else { $installs | Select-Object -First 1 }

if (-not $pick) {
    Write-Host "Nie mam wersji zaczynającej się od '$Version'. Dostępne:" -ForegroundColor Red
    $installs | ForEach-Object { Write-Host "   $($_.Version)  ->  $($_.Path)" }
    return
}

# Na początek PATH, żeby wygrać z wpisami systemowymi (patrz opis wyżej).
$env:PATH = "$($pick.Path);$env:PATH"

Write-Host "Node w tej sesji: v$($pick.Version)" -ForegroundColor Green
Write-Host "  node -> $((Get-Command node).Source)"
Write-Host "  npm  -> $((Get-Command npm).Source)"
Write-Host "  npm    $(& npm --version 2>$null)"
if ($installs.Count -gt 1) {
    $other = ($installs | Where-Object { $_.Version -ne $pick.Version } | ForEach-Object { $_.Version }) -join ", "
    Write-Host "Inne dostępne: $other   (przełącz: . .\node-env.ps1 -Version <numer>)" -ForegroundColor DarkGray
}
