"""Testy BEZPIECZNIKA (cm_auth) — jedyna rzecz chroniąca konto produkcyjne klienta.

Sprawdzają samą regułę URI/ciała, bez sieci i bez tokenu: `_check_uri` i `_check_body`
są czystymi funkcjami. Kształty adresów wzięte z realnych żądań (discovery v5).
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cm_auth

passed = failed = 0
OK_P = sorted(cm_auth.ALLOWED_PROFILE_IDS)[0]        # 9556074 (Cube Group, testowy)
OK_A = sorted(cm_auth.ALLOWED_ADVERTISER_IDS)[0]     # 11992166 (advertiser testowy)
MBANK_P = "9765911"                                  # profil produkcyjny klienta
MBANK_A = "9081506"                                  # advertiser produkcyjny z tagów
BASE = "https://dfareporting.googleapis.com/dfareporting/v5"
UPLOAD = "https://dfareporting.googleapis.com/upload/dfareporting/v5"


def check(name, got, want):
    global passed, failed
    ok = got == want
    passed += ok; failed += not ok
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"        got={got!r}\n        want={want!r}")


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


print("profil w ścieżce:")
check("profil testowy przechodzi", blocked(f"{BASE}/userprofiles/{OK_P}/placements"), False)
check("PROFIL PRODUKCYJNY KLIENTA zablokowany",
      blocked(f"{BASE}/userprofiles/{MBANK_P}/placements"), True)

print("\nadvertiser w ścieżce i w filtrze:")
check("advertiser testowy przechodzi",
      blocked(f"{BASE}/userprofiles/{OK_P}/advertisers/{OK_A}"), False)
check("obcy advertiser zablokowany",
      blocked(f"{BASE}/userprofiles/{OK_P}/advertisers/{MBANK_A}"), True)
check("listowanie WSZYSTKICH advertiserów zablokowane",
      blocked(f"{BASE}/userprofiles/{OK_P}/advertisers"), True)
check("filtr advertiserIds w zapytaniu pilnowany",
      blocked(f"{BASE}/userprofiles/{OK_P}/placements?advertiserIds={MBANK_A}"), True)

print("\nUPLOAD ASSETU KREACJI — advertiserId jest SEGMENTEM ŚCIEŻKI:")
# Dziura znaleziona przed pierwszym realnym zapisem programmatica: ten kształt adresu
# nie pasuje ani do `/advertisers/{id}`, ani do filtra w zapytaniu, a ciało żądania
# niesie tylko `assetIdentifier` — więc nic go nie sprawdzało.
check("upload na advertisera testowego przechodzi",
      blocked(f"{UPLOAD}/userprofiles/{OK_P}/creativeAssets/{OK_A}/creativeAssets"
              "?uploadType=multipart"), False)
check("upload na OBCEGO advertisera zablokowany",
      blocked(f"{UPLOAD}/userprofiles/{OK_P}/creativeAssets/{MBANK_A}/creativeAssets"
              "?uploadType=multipart"), True)
check("...także bez prefiksu /upload (ścieżka nie-mediowa)",
      blocked(f"{BASE}/userprofiles/{OK_P}/creativeAssets/{MBANK_A}/creativeAssets"), True)
check("...i gdy profil też jest produkcyjny",
      blocked(f"{UPLOAD}/userprofiles/{MBANK_P}/creativeAssets/{MBANK_A}/creativeAssets"), True)

print("\nadvertiserId w CIELE żądania:")
check("ciało z advertiserem testowym przechodzi",
      blocked_body('{"name": "x", "advertiserId": "' + OK_A + '"}'), False)
check("ciało z obcym advertiserem zablokowane",
      blocked_body('{"name": "x", "advertiserId": "' + MBANK_A + '"}'), True)
check("ciało bez advertiserId nie jest sprawdzane (obiekty account-level)",
      blocked_body('{"name": "CG_WP"}'), False)

print("\nniezmienniki bezpiecznika:")
check("allowlista profili to WYŁĄCZNIE konto testowe",
      sorted(cm_auth.ALLOWED_PROFILE_IDS), ["9556074"])
check("allowlista advertiserów to WYŁĄCZNIE advertiser testowy",
      sorted(cm_auth.ALLOWED_ADVERTISER_IDS), ["11992166"])
check("zapisy są domyślnie WYŁĄCZONE", cm_auth.WRITE_ENABLED, False)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
