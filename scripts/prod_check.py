"""Weryfikacja wyszukiwarki na PRODUKCJI — narzędzie pośrednie, TYLKO DO ODCZYTU.

Po co: zanim cokolwiek zapiszemy na koncie klienta, trzeba wiedzieć, czy narzędzie
w ogóle poprawnie rozpoznaje advertisera z linku i czy trafia w tę kampanię, w którą
trafiłby trafficker. `advertiser_map.json` jest prototypem — 14 reguł produkcyjnych,
z czego trzy z ostrzeżeniem („anchor guessed", konflikt `intensive`) — a sprawdzić je
da się wyłącznie na żywym koncie.

Świadomie osobny skrypt, a nie tryb `serve.py`: nie ma tu ŻADNEJ ścieżki zapisu, więc
nie da się nim nic zepsuć nawet przez pomyłkę. Bezpiecznik i tak stoi (profil z `CM_ENV`,
`writes: false` na produkcji), ale narzędzie, które fizycznie nie woła writerów, jest
lepszym pierwszym kontaktem z kontem klienta niż całe UI.

Użycie (produkcja — zmienna środowiskowa, świadomie):
  CM_ENV=prod py scripts/prod_check.py <link> [<link> ...]
  CM_ENV=prod py scripts/prod_check.py --campaign <campaignId> [--site <nazwa>]
  CM_ENV=prod py scripts/prod_check.py --file linki.txt

Bez `CM_ENV` działa na koncie testowym — do porównania „jak to wygląda po obu stronach".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matcher as M
import cm_env
from cm_auth import service
from cm_read import fetch_state, existing_tree
from match_link import MAP_PATH, _fetch_campaign_lps, advertiser_for


def _rules():
    with open(MAP_PATH, encoding="utf-8") as f:
        return json.load(f)["rules"]


def _head():
    d = cm_env.describe()
    print(f"\n=== {d['label']}  (profil {d['profileId']}) ===")
    print(f"    advertiserzy: {d['advertisers']}    zapisy: "
          f"{'DOZWOLONE' if d['writes'] else 'ZABLOKOWANE (tylko odczyt)'}")
    if d["isProd"]:
        print("    UWAGA: to konto KLIENTA. Ten skrypt tylko czyta.")
    print()
    return d


def check_links(svc, links):
    """Dla każdego linku: rozwiązany advertiser + dopasowanie kampanii.

    Cache'ujemy strony docelowe per advertiser, bo `_fetch_campaign_lps` przechodzi
    wszystkie kampanie advertisera — przy liście kilkunastu linków z jednego advertisera
    bez tego czekałoby się kilkanaście razy na to samo.
    """
    rules = _rules()
    lps_cache, problems = {}, []
    for link in links:
        print(f"── {link}")
        rule = M.resolve_advertiser(link, rules)
        if not rule:
            print("   ❌ ADVERTISER NIEROZPOZNANY — brak pasującej reguły w mapie")
            problems.append((link, "brak reguły advertisera"))
            print()
            continue
        anchor = rule.get("anchor") or []
        print(f"   advertiser: {rule.get('advertiser')}  (id {rule.get('advertiserId')}, "
              f"anchor {anchor or '—'})")
        if rule.get("_note"):
            print(f"   ⚠️  reguła oznaczona jako niepewna: {rule['_note']}")
        try:
            adv = advertiser_for(rule)
        except RuntimeError as e:
            print(f"   ❌ {e}")
            problems.append((link, str(e)))
            print()
            continue
        if adv not in lps_cache:
            lps_cache[adv] = _fetch_campaign_lps(svc, cm_env.profile_id(), adv)
        lps = lps_cache[adv]
        print(f"   stron docelowych u tego advertisera: {len(lps)}")
        ranked, suggest_new = M.match_campaigns(link, anchor, lps)
        print(f"   pozostała ścieżka: /{'/'.join(M.remaining_path(link, anchor) or [])}")
        if suggest_new:
            print("   → NOWA kampania (nic nie przeszło progu dopasowania)")
            problems.append((link, "brak dopasowania kampanii"))
        else:
            b = ranked[0]
            print(f"   → KAMPANIA {b['campaignId']}  {b['campaignName']!r}")
            print(f"     bo: {b['why']}  (po LP {b.get('lpName')})")
        for c in ranked[1:4]:
            mark = "  " if c.get("enough") else "· "
            print(f"     {mark}kandydat: {c['campaignName']!r} — {c['why']}")
        print()
    return problems


def show_campaign(svc, campaign_id, site=None):
    """Struktura kampanii tak, jak widzi ją narzędzie: Site → placement → ad → kreacje."""
    pid = cm_env.profile_id()
    camp = svc.campaigns().get(profileId=pid, id=campaign_id).execute()
    adv = camp.get("advertiserId")
    print(f"KAMPANIA {campaign_id}  {camp.get('name')!r}")
    print(f"   advertiser {adv} | {camp.get('startDate')} .. {camp.get('endDate')} | "
          f"default LP: {camp.get('defaultLandingPageId')}")
    state = fetch_state(svc, pid, adv, campaign_id)
    tree = existing_tree(state)
    lps = state.get("lps_by_name") or {}
    print(f"\n   strony docelowe kampanii ({len(lps)}): "
          f"{', '.join(sorted(lps)) if lps else '—'}")
    n_ads = n_cre = 0
    for s in sorted(tree):
        if site and s != site:
            continue
        print(f"\n   ▸ SITE {s}")
        for plc in sorted(tree[s]):
            ads = tree[s][plc]
            print(f"      • {plc}  ({len(ads)} adów)")
            for ad in sorted(ads):
                cres = sorted(ads[ad])
                n_ads += 1
                n_cre += len(cres)
                print(f"          {ad:<34} {', '.join(cres) if cres else '(brak kreacji)'}")
    print(f"\n   razem: {n_ads} adów, {n_cre} przypisań kreacji "
          f"(= tyle wierszy miałby arkusz tagów)")


def main(argv):
    args = list(argv)
    d = _head()
    svc = service()                      # read-only; bezpiecznik i tak pilnuje profilu

    if "--campaign" in args:
        i = args.index("--campaign")
        cid = args[i + 1]
        site = args[args.index("--site") + 1] if "--site" in args else None
        show_campaign(svc, cid, site)
        return 0

    links = []
    if "--file" in args:
        with open(args[args.index("--file") + 1], encoding="utf-8") as f:
            links = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    links += [a for a in args if a.startswith("http")]
    if not links:
        print(__doc__)
        return 2

    problems = check_links(svc, links)
    print("=" * 70)
    print(f"sprawdzono {len(links)} link(ów), problemów: {len(problems)}")
    for link, why in problems:
        print(f"   ❌ {why}: {link}")
    if d["isProd"] and not problems:
        print("   wszystkie linki rozpoznane — mapa advertiserów działa na tych przykładach")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
