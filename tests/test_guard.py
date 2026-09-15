"""Testy BEZPIECZNIKA (cm_auth + cm_env) — jedyna rzecz chroniąca konta przed sobą.

Sprawdzają samą regułę URI/ciała, bez sieci i bez tokenu: `_check_uri` i `_check_body`
są czystymi funkcjami. Kształty adresów wzięte z realnych żądań (discovery v5).

Od 15.09.2026 bezpiecznik zależy od ŚRODOWISKA (`CM_ENV`), więc każdy zestaw reguł jest
sprawdzany w OBU: testowe ma zamkniętą listę advertiserów, produkcja otwartą — ale obie
są zamknięte na swój profil i obie zabraniają DELETE.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cm_auth
import cm_env

passed = failed = 0
TEST_P = "9556074"      # Cube Group (testowy)
TEST_A = "11992166"     # advertiser testowy
MBANK_P = "9765911"     # profil produkcyjny klienta
MBANK_A = "9081506"     # advertiser produkcyjny z realnych tagów (CG Indywidualny - Ubezpieczenia)
OTHER_A = "9080582"     # inny advertiser produkcyjny
BASE = "https://dfareporting.googleapis.com/dfareporting/v5"
UPLOAD = "https://dfareporting.googleapis.com/upload/dfareporting/v5"


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


def use_env(name, prod_writes=False):
    """Przełącz środowisko tak, jak robi to uruchomienie procesu."""
    os.environ["CM_ENV"] = name
    if prod_writes:
        os.environ["CM_PROD_WRITES"] = "1"
    else:
        os.environ.pop("CM_PROD_WRITES", None)


def blocked(uri):
    """True, gdy bezpiecznik odrzuca ten adres."""
    try:
        cm_auth._check_uri(uri)
        return False
    except RuntimeError:
        return True


def blocked_body(body):
    try:
        cm_auth._check_body(body)
        return False
    except RuntimeError:
        return True


# =============================== ŚRODOWISKO TESTOWE ==========================
use_env("test")
print("ŚRODOWISKO TESTOWE — profil w ścieżce:")
check("profil testowy przechodzi", blocked(f"{BASE}/userprofiles/{TEST_P}/placements"), False)
check("PROFIL PRODUKCYJNY KLIENTA zablokowany",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/placements"), True)

print("\nadvertiser w ścieżce i w filtrze:")
check("advertiser testowy przechodzi",
      blocked(f"{BASE}/userprofiles/{TEST_P}/advertisers/{TEST_A}"), False)
check("obcy advertiser zablokowany",
      blocked(f"{BASE}/userprofiles/{TEST_P}/advertisers/{MBANK_A}"), True)
check("listowanie WSZYSTKICH advertiserów zablokowane",
      blocked(f"{BASE}/userprofiles/{TEST_P}/advertisers"), True)
check("filtr advertiserIds w zapytaniu pilnowany",
      blocked(f"{BASE}/userprofiles/{TEST_P}/placements?advertiserIds={MBANK_A}"), True)

print("\nUPLOAD ASSETU KREACJI — advertiserId jest SEGMENTEM ŚCIEŻKI:")
# Dziura znaleziona przed pierwszym realnym zapisem programmatica: ten kształt adresu
# nie pasuje ani do `/advertisers/{id}`, ani do filtra w zapytaniu, a ciało żądania
# niesie tylko `assetIdentifier` — więc nic go nie sprawdzało.
check("upload na advertisera testowego przechodzi",
      blocked(f"{UPLOAD}/userprofiles/{TEST_P}/creativeAssets/{TEST_A}/creativeAssets"
              "?uploadType=multipart"), False)
check("upload na OBCEGO advertisera zablokowany",
      blocked(f"{UPLOAD}/userprofiles/{TEST_P}/creativeAssets/{MBANK_A}/creativeAssets"
              "?uploadType=multipart"), True)
check("...także bez prefiksu /upload (ścieżka nie-mediowa)",
      blocked(f"{BASE}/userprofiles/{TEST_P}/creativeAssets/{MBANK_A}/creativeAssets"), True)
check("...i gdy profil też jest produkcyjny",
      blocked(f"{UPLOAD}/userprofiles/{MBANK_P}/creativeAssets/{MBANK_A}/creativeAssets"), True)

print("\nadvertiserId w CIELE żądania:")
check("ciało z advertiserem testowym przechodzi",
      blocked_body('{"name": "x", "advertiserId": "' + TEST_A + '"}'), False)
check("ciało z obcym advertiserem zablokowane",
      blocked_body('{"name": "x", "advertiserId": "' + MBANK_A + '"}'), True)
check("ciało bez advertiserId nie jest sprawdzane (obiekty account-level)",
      blocked_body('{"name": "CG_WP"}'), False)

print("\nniezmienniki środowiska testowego:")
check("allowlista profili to WYŁĄCZNIE konto testowe",
      sorted(cm_auth.allowed_profile_ids()), [TEST_P])
check("allowlista advertiserów to WYŁĄCZNIE advertiser testowy",
      sorted(cm_auth.allowed_advertiser_ids()), [TEST_A])
check("zapisy są domyślnie WYŁĄCZONE", cm_auth.WRITE_ENABLED, False)
check("środowisko testowe DOPUSZCZA zapisy (po service(read_only=False))",
      cm_env.writes_allowed()[0], True)

# ============================= ŚRODOWISKO PRODUKCYJNE ========================
use_env("prod")
print("\nŚRODOWISKO PRODUKCYJNE — profil zamyka się na MBanku, nie otwiera na wszystko:")
check("profil produkcyjny przechodzi",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/placements"), False)
# odwrotny kierunek jest równie ważny: praca na produkcji nie może po cichu dotknąć
# konta testowego, bo wtedy „sprawdziłem na teście" przestaje cokolwiek znaczyć
check("PROFIL TESTOWY zablokowany, gdy pracujemy na produkcji",
      blocked(f"{BASE}/userprofiles/{TEST_P}/placements"), True)

print("\nadvertiserzy: wszyscy TEGO profilu (decyzja usera — advertiser bierze się z linku):")
check("advertiser produkcyjny przechodzi",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/advertisers/{MBANK_A}"), False)
check("inny advertiser produkcyjny też przechodzi",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/advertisers/{OTHER_A}"), False)
check("listowanie advertiserów DOZWOLONE (bez tego nie da się zweryfikować mapy)",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/advertisers"), False)
check("filtr advertiserIds przechodzi dla dowolnego advertisera",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/placements?advertiserIds={OTHER_A}"), False)
check("upload assetu dla advertisera produkcyjnego przechodzi",
      blocked(f"{UPLOAD}/userprofiles/{MBANK_P}/creativeAssets/{MBANK_A}/creativeAssets"), False)
check("ciało z dowolnym advertiserem produkcyjnym przechodzi",
      blocked_body('{"name": "x", "advertiserId": "' + OTHER_A + '"}'), False)
# ...ale advertiser CUDZEGO konta nadal nie ma jak wejść — chroni go reguła PROFILU
check("advertiser testowy pod profilem produkcyjnym: blokuje go profil w ścieżce",
      blocked(f"{BASE}/userprofiles/{TEST_P}/advertisers/{TEST_A}"), True)

print("\nprodukcja startuje jako TYLKO DO ODCZYTU:")
ok, why = cm_env.writes_allowed()
check("zapisy zablokowane bez CM_PROD_WRITES", ok, False)
check("...z powodem, który mówi co zrobić", "CM_PROD_WRITES=1" in why, True)
try:
    cm_auth.service(read_only=False)
    got = "przepuścił"
except RuntimeError as e:
    got = "SAFETY guard" in str(e)
check("service(read_only=False) ODMAWIA na produkcji (bez sieci, przed budową klienta)",
      got, True)
check("odczyt na produkcji jest dozwolony",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/campaigns"), False)

use_env("prod", prod_writes=True)
check("CM_PROD_WRITES=1 odblokowuje zapisy świadomie", cm_env.writes_allowed()[0], True)

print("\nmapa Site jest per środowisko (te same źródła nazywają się inaczej):")
check("WP na produkcji to WP.pl", cm_env.site_name("WP.pl", "prod"), "WP.pl")
check("WP na teście to CG_WP (WP.pl tam NIE ISTNIEJE)",
      cm_env.site_name("WP.pl", "test"), "CG_WP")
check("źródło bez override zostaje bez zmian",
      cm_env.site_name("CG_GDN", "test"), "CG_GDN")

print("\nreguły wspólne dla OBU środowisk:")
for env in ("test", "prod"):
    use_env(env)
    check(f"[{env}] DELETE nie jest dozwolony nigdzie",
          "DELETE is never allowed" in open(
              os.path.join(os.path.dirname(__file__), "..", "scripts", "cm_auth.py"),
              encoding="utf-8").read(), True)
    check(f"[{env}] profil obcego konta zablokowany",
          blocked(f"{BASE}/userprofiles/{'9556074' if env == 'prod' else '9765911'}/ads"),
          True)

# literówka w CM_ENV nie może po cichu cofnąć się do żadnego konta
os.environ["CM_ENV"] = "produkcja"
try:
    cm_env.env_name()
    got = "przepuścił"
except RuntimeError as e:
    got = "nie istnieje" in str(e)
check("nieznane CM_ENV podnosi błąd, zamiast cicho wybrać środowisko", got, True)
use_env("test")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
