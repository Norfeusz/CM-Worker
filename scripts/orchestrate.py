"""Orchestrator: execute the full chain for a matched campaign.

For a proposal targeting one campaign, create in dependency order — skipping
whatever already exists:
   LP (line, if new) -> Site (reuse/create) -> Creative (once per line)
   -> Placement (reuse/create) -> Ad (references placement + creative + LP)

dry_run=True (default) prints every payload and touches nothing. Real writes
require dry_run=False AND service(read_only=False); the cm_auth guard still
restricts everything to the test profile+advertiser.

Usage:
  py scripts/orchestrate.py --demo            # dry-run full chain (campaign 36430023)
  py scripts/orchestrate.py --demo --execute  # really write it
  py scripts/orchestrate.py --demo --execute --dims 300x250   # only one dimension
"""
import datetime
import os
import sys

import matcher as M
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import build_proposal as B
import cm_write as W
from cm_auth import service
from cm_read import fetch_state, _paginate
from match_link import _fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER


class Orchestrator:
    def __init__(self, svc, profile_id, advertiser_id, campaign, dry_run=True):
        self.svc = svc
        self.pid = profile_id
        self.adv = advertiser_id
        self.campaign = campaign
        self.cid = campaign.get("id")
        self.dry = dry_run
        self.log = []
        # a campaign to be created has no id/dates yet -> project defaults (start today,
        # end +5y); an existing one keeps its own flight so placements/ads fit inside it
        self.is_new_campaign = campaign.get("status") == "new" or not self.cid
        if self.is_new_campaign:
            self.start_date, self.end_date = W.campaign_dates(campaign.get("startDate"))
        else:
            self.start_date, self.end_date = campaign["startDate"], campaign["endDate"]
        # ad startTime must not be in the past -> use now+5min (still within campaign)
        now = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
        self.start_time = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.end_time = f"{self.end_date}T23:59:00.000Z"

    def _rec(self, action, kind, name, rid=None, detail=""):
        self.log.append({"action": action, "kind": kind, "name": name, "id": rid, "detail": detail})
        idp = f" id={rid}" if rid else ""
        print(f"  {action:8} {kind:11} {name:28}{idp}   {detail}")

    @staticmethod
    def _lp_key(proposal, cr):
        """A creative's own (lpName, lpUrl) override, or the shared line LP by default."""
        if cr.get("lpName"):
            return cr["lpName"], cr.get("lpUrl") or ""
        return proposal["line"]["lpName"], proposal["line"]["url"] or ""

    @staticmethod
    def serving_names(proposal):
        """Nazwy placementów SERWUJĄCYCH (programmatic) w propozycji.

        Używane w trzech miejscach: `run()` ustawia dla nich LP `-default` jako default
        kampanii, `_run_serving()` je obsługuje, a `/api/commit` na razie ODMAWIA na nich
        realnego zapisu — writer istnieje, ale nie przeszedł jeszcze ani jednego przebiegu
        na żywym koncie, a pomyłka zostawia na nim nieusuwalne kreacje i assety.
        """
        return sorted({pl.get("name") for pl in proposal.get("placements") or []
                       if pl.get("serving")})

    @staticmethod
    def lp_urls_missing(proposal, state):
        """Landing pages the proposal wants to CREATE but gives no address for.

        CM360 odrzuca `advertiserLandingPages.insert` bez url (błąd 18112 „URL strony
        docelowej jest wymagany”) — i robi to w ŚRODKU zapisu, gdy kampania i część LP
        już powstały. Dlatego to musi być sprawdzone PRZED pierwszym zapisem, dokładnie
        jak brakujący Site.

        Pusty url jest dopuszczalny tylko dla LP, które JUŻ jest w kampanii: wtedy
        `_ensure_lp` znajduje je po nazwie i nic nie tworzy. Stąd potrzebny `state` —
        bez niego nie da się odróżnić „wskazujemy istniejące LP” od „tworzymy LP bez
        adresu”.

        Zwraca {lpName: [gdzie to jest użyte]} — nazwy miejsc, żeby użytkownik wiedział,
        któremu creative dopisać adres, a nie tylko że „czegoś brakuje”.
        """
        # bez wzgl. na wielkość liter — tak samo jak `_ensure_lp`, inaczej narzędzie
        # żądałoby adresu dla LP, które na koncie JEST, tylko pisane inaczej
        known = {k.lower() for k in (state.get("lps_by_name") or {})}
        bad = {}
        pairs = [(proposal["line"]["lpName"], proposal["line"].get("url"), "LP linii")]
        for pl in proposal.get("placements") or []:
            for ad in pl.get("ads") or []:
                for cr in ad.get("creatives") or []:
                    name, url = Orchestrator._lp_key(proposal, cr)
                    pairs.append((name, url, f"{pl['name']}/{ad['name']}/{cr['name']}"))
        for name, url, where in pairs:
            if not (url or "").strip() and (name or "").lower() not in known:
                bad.setdefault(name, [])
                if where not in bad[name]:
                    bad[name].append(where)
        return bad

    @staticmethod
    def duplicate_lp_urls(proposal, state):
        """Strony docelowe, które ZAŁOŻYMY na adresie już obsługiwanym przez inną stronę
        tej kampanii. Zwraca `{nowaNazwa: [istniejące nazwy na tym adresie]}`.

        Po co osobne sprawdzenie: to jest legalny ruch i czasem zamierzony — tak działa
        „dodaj jako nową linię", gdy kolejna odsłona kampanii idzie na ten sam adres.
        Ale **LP w CM360 się nie usuwa**, więc pomyłka zostaje na koncie klienta na zawsze,
        a z samego drzewa nie widać, że coś takiego się stanie. Dlatego zapis pyta o zgodę
        zamiast milczeć — i pyta o SYTUACJĘ, nie o to, skąd się wzięła: tak samo złapie
        ręczną zmianę nazwy w edytorze linii i operację agenta.

        Łapiemy to DWIEMA drogami, bo żadna sama nie wystarcza:

        1. `replacesLp` na linii — ślad po „dodaj jako nową linię": nazwa strony, którą ta
           linia dotąd współdzieliła. To jest sygnał PEWNY, bo pochodzi z dopasowania
           zrobionego przez samo narzędzie przy budowaniu propozycji.
        2. identyczny adres — gdy ktoś wpisał nazwę ręcznie, bez konwersji.

        Porównanie adresów jest DOKŁADNE (po obcięciu spacji), świadomie z parametrami:
        `?utm_source=facebook` i `?utm_source=gdn` to dwie różne strony i tak mają być
        trafficowane — zwinięcie parametrów robiłoby fałszywe alarmy na każdym zleceniu
        wieloźródłowym. Dlatego właśnie potrzebny jest punkt 1: realny przypadek z 17.09.2026
        miał na koncie `utm_medium=cpc`, którego nie było w linku ze zlecenia, więc po samych
        adresach wyszły DWIE różne strony i bramka milczała.
        """
        by_name = state.get("lps_by_name") or {}
        by_url = {}
        for n, u in (state.get("lp_urls_by_name") or {}).items():
            key = (u or "").strip()
            if key:
                by_url.setdefault(key, []).append(n)

        out = {}
        for ln in ([proposal.get("line")] if proposal.get("line") else []) \
                + (proposal.get("lines") or []):
            was, now = ln.get("replacesLp"), ln.get("lpName")
            if not was or not now or was == now:
                continue
            if Orchestrator._find_lp(by_name, was)[1] and not Orchestrator._find_lp(by_name, now)[1]:
                out.setdefault(now, set()).add(was)

        pairs = [(proposal.get("line", {}).get("lpName"),
                  proposal.get("line", {}).get("url"))]
        for pl in proposal.get("placements") or []:
            for ad in pl.get("ads") or []:
                for cr in ad.get("creatives") or []:
                    pairs.append(Orchestrator._lp_key(proposal, cr))
        for name, url in pairs:
            if not name or not (url or "").strip():
                continue
            if Orchestrator._find_lp(by_name, name)[1]:
                continue                      # LP już istnieje — niczego nie zakładamy
            clash = [n for n in by_url.get((url or "").strip(), [])
                     if n.lower() != name.lower()]
            if clash:
                out.setdefault(name, set()).update(clash)
        return {k: sorted(v) for k, v in out.items()}

    @staticmethod
    def _find_lp(by_name, name):
        """(istniejąca nazwa, id) szukane BEZ WZGLĘDU NA WIELKOŚĆ LITER, inaczej (None, None).

        Konta klienta nie trzymają się jednej pisowni: kampania 35398313 ma 14 stron
        `Linia4-FB-Konto` z wielkiej litery i jedną `linia7-FB-rozchodniak` z małej.
        Nasza konwencja generuje małą, a porównanie dokładne znaczyło, że narzędzie
        utworzyłoby DRUGĄ stronę docelową na ten sam adres zamiast użyć istniejącej —
        i dopiero wtedy byłoby widać problem, bo w CM360 LP się nie usuwa.

        Szukamy więc bez względu na wielkość liter, ale NIE zmieniamy niczego na koncie:
        zwracana jest nazwa taka, jaka tam stoi, i to jej id wchodzi do struktury.
        """
        if name in by_name:
            return name, by_name[name]
        low = (name or "").lower()
        return next(((k, v) for k, v in by_name.items() if k.lower() == low), (None, None))

    def _ensure_lp(self, name, url, state):
        """Resolve an existing landing page by name (in campaign) or name+url (on the
        advertiser), else create it. Returns (lpId, alreadyInThisCampaign)."""
        found, lp_id = self._find_lp(state["lps_by_name"], name)
        in_campaign = found is not None
        if not lp_id:
            adv = {k[0]: v for k, v in state["adv_lp_by_name_url"].items() if k[1] == url}
            found, lp_id = self._find_lp(adv, name)
        if lp_id:
            self._rec("REUSE", "landingPage", found, lp_id,
                      "" if found == name else f"(na koncie pisane {found!r}, u nas {name!r})")
        else:
            r = W.landing_page(self.svc, self.pid, self.adv, name, url, dry_run=self.dry)
            lp_id = r.get("id", "(new)")
            self._rec("CREATE", "landingPage", name, r.get("id"), url)
        return lp_id, in_campaign

    def _create_campaign(self, default_lp_id):
        """Create the campaign and adopt its id for everything that follows."""
        r = W.campaign(self.svc, self.pid, self.adv, self.campaign["name"], default_lp_id,
                       self.start_date, self.end_date, dry_run=self.dry)
        self.cid = r.get("id", "(new)")
        self.campaign = dict(self.campaign, id=self.cid, startDate=self.start_date,
                             endDate=self.end_date, defaultLandingPageId=default_lp_id)
        self._rec("CREATE", "campaign", self.campaign["name"], r.get("id"),
                  f"{self.start_date}..{self.end_date}, default LP={default_lp_id}, "
                  f"brak treści politycznych")

    def _run_serving(self, pl, site, site_ids, lp_ids, proposal, state):
        """Placement, na którym CM SERWUJE kreacje (programmatic).

        Inna kolejność niż tracking, bo kreacja nie istnieje bez materiału:
          upload assetu -> kreacja DISPLAY -> powiązanie z kampanią -> placement
          z listą wymiarów -> JEDEN ad `AD_SERVING_STANDARD_AD` ze wszystkimi kreacjami.

        Adów `{wymiar} Default Web Ad` tu nie tworzymy — robi je CM sam po zadeklarowaniu
        wymiarów na placemencie i bierze dla nich DOMYŚLNĄ stronę docelową kampanii
        (ustawianą w `run()`).
        """
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
        import repack

        ad = (pl.get("ads") or [{}])[0]
        cres = ad.get("creatives") or []
        creative_ids = []
        for cr in cres:
            cname = cr["name"]
            cid_ = state["creatives_by_name"].get(cname)
            if cid_:
                # REUSE po nazwie jak wszędzie, ale tu warto na to spojrzeć w dry-runie:
                # kreacja serwująca niesie materiał, a nazwa (`300x250`) jest na tyle
                # ogólna, że mogłaby trafić w cudzą.
                self._rec("REUSE", "creative", cname, cid_, "serwująca — materiał BEZ zmian")
            else:
                try:
                    fname, data = repack.unit_asset(cr.get("unit", {}).get("_zip"),
                                                    cr.get("unit") or {})
                except Exception as e:
                    self._rec("SKIP", "creative", cname, None, f"brak materiału: {e}")
                    continue
                a = W.creative_asset(self.svc, self.pid, self.adv, fname, data,
                                     dry_run=self.dry)
                self._rec("UPLOAD", "asset", fname, None, f"{len(data)} B -> {cname}")
                r = W.display_creative(self.svc, self.pid, self.adv, cname, cname,
                                       a["assetIdentifier"], click_tags=a.get("clickTags"),
                                       dry_run=self.dry)
                cid_ = r.get("id", "(new)")
                self._rec("CREATE", "creative", cname, r.get("id"), "DISPLAY + asset PRIMARY")
            W.associate_creative_to_campaign(self.svc, self.pid, self.cid, cid_,
                                             dry_run=self.dry)
            self._rec("LINK", "campaign-creative", cname, cid_, f"-> campaign {self.cid}")
            creative_ids.append(cid_)

        p_id = state["placements"].get((site, pl["name"]))
        if p_id:
            self._rec("REUSE", "placement", pl["name"], p_id, f"site={site}")
        else:
            r = W.serving_placement(self.svc, self.pid, self.cid, site_ids[site],
                                    pl["name"], pl.get("sizes") or [], self.start_date,
                                    dry_run=self.dry)
            p_id = r.get("id", "(new)")
            self._rec("CREATE", "placement", pl["name"], r.get("id"),
                      f"site={site}, wymiary: {len(pl.get('sizes') or [])}")

        if not creative_ids:
            self._rec("SKIP", "ad", ad.get("name") or "Display", None, "brak kreacji")
            return
        ad_key = (site, pl["name"], ad.get("name") or "Display")
        if state["ads"].get(ad_key):
            self._rec("NO-OP", "ad", ad_key[2], state["ads"][ad_key], "już istnieje")
            return
        lp_name, _ = self._lp_key(proposal, cres[0])
        r = W.standard_ad(self.svc, self.pid, self.cid, ad_key[2], p_id, creative_ids,
                          lp_ids.get(lp_name), self.start_time, self.end_time,
                          dry_run=self.dry)
        self._rec("CREATE", "ad", ad_key[2], r.get("id"),
                  f"AD_SERVING_STANDARD_AD, {len(creative_ids)} kreacji -> LP {lp_name}")

    def run(self, proposal, state):
        head = (f"NEW CAMPAIGN '{self.campaign['name']}'" if self.is_new_campaign
                else f"CAMPAIGN {self.cid} '{self.campaign['name']}'")
        print(f"{head}  ({'DRY-RUN' if self.dry else 'REAL WRITE'})  "
              f"dates {self.start_date}..{self.end_date}\n")
        # Sites are per PLACEMENT, not per order: jedno zlecenie może objąć kilka źródeł
        # (paczka z folderami `GDN/` i `Programmatic/`), a każde źródło ma swój Site.
        # `proposal["site"]` zostaje jako Site źródła głównego — fallback dla propozycji
        # zbudowanych zanim placementy nosiły własny Site.
        main_site = proposal["site"]["name"]
        site_of = lambda pl: pl.get("site") or main_site
        line_lp_name = proposal["line"]["lpName"]

        # 1) landing pages: resolve/create every DISTINCT LP referenced anywhere in the
        # proposal — normally just the shared line LP, but a creative may override its
        # own (e.g. linia4-słońce / linia4-niebo pointing at two different URLs).
        lp_wanted = dict([(line_lp_name, proposal["line"]["url"] or "")] + [
            self._lp_key(proposal, cr) for pl in proposal["placements"]
            for a in pl["ads"] for cr in a["creatives"]])

        # 1a) the LINE LP goes first and alone: creating a campaign requires a
        # defaultLandingPageId, so for a NEW campaign it must exist beforehand.
        lp_ids = {}
        line_lp_id, line_in_campaign = self._ensure_lp(
            line_lp_name, lp_wanted.pop(line_lp_name), state)
        lp_ids[line_lp_name] = line_lp_id

        # 1b) the campaign itself — for a new one, passing the line LP as its default
        # IS the registration, so no default-cycle is needed for it
        if self.is_new_campaign:
            self._create_campaign(line_lp_id)
            line_in_campaign = True
        # LP linii programmatica (`…-default`) MUSI zostać domyślną stroną kampanii:
        # ady `{wymiar} Default Web Ad` tworzy sam CM po zadeklarowaniu wymiarów na
        # placemencie i bierze dla nich właśnie default kampanii. Nie ma ich w naszym
        # drzewie, więc bez tego kierowałyby w LP poprzedniego zlecenia.
        needs_default = bool(self.serving_names(proposal))
        if not line_in_campaign or needs_default:
            first_line = not self.campaign.get("defaultLandingPageId")
            make_default = first_line or needs_default
            W.add_lp_to_campaign(self.svc, self.pid, self.cid, line_lp_id,
                                 make_default=make_default, dry_run=self.dry)
            self._rec("REGISTER", "campaign-LP", line_lp_name, line_lp_id,
                      "jako default kampanii (programmatic)" if needs_default
                      else "as default (first line)" if first_line
                      else "added to campaign list")

        # 1c) any per-creative LP overrides — never eligible to become the default
        for name, url in lp_wanted.items():
            lp_id, in_campaign = self._ensure_lp(name, url, state)
            if not in_campaign:
                W.add_lp_to_campaign(self.svc, self.pid, self.cid, lp_id,
                                     make_default=False, dry_run=self.dry)
                self._rec("REGISTER", "campaign-LP", name, lp_id, "added to campaign list")
            lp_ids[name] = lp_id

        # 2) sites (reuse; creation is an edge case that needs directory-site linkage).
        # Każdy Site raz, w kolejności występowania — main_site pierwszy, żeby log czytał
        # się tak samo jak dotąd dla zwykłego zlecenia jednoźródłowego.
        site_ids = {}
        for site in list(dict.fromkeys([main_site] + [site_of(pl) for pl
                                                      in proposal["placements"]])):
            site_id = state["sites_by_name"].get(site)
            if site_id:
                self._rec("REUSE", "site", site, site_id)
            else:
                r = W.create_site(self.svc, self.pid, site, dry_run=self.dry)
                site_id = r.get("id", "(new)")
                self._rec("CREATE", "site", site, r.get("id"),
                          "Site Directory: znajdź istniejący albo dodaj nowy + podepnij do konta")
            site_ids[site] = site_id

        # 3) creatives: ensure EVERY distinct creative name used anywhere in the
        # proposal exists + is campaign-associated (an ad can carry several, e.g.
        # linia4-słońce + linia4-niebo on the same dimension).
        # Kreacje placementów SERWUJĄCYCH pomijamy tutaj świadomie: to nie są szablony
        # 1x1, tylko realne banery z wgranym materiałem — powstają w `_run_serving()`
        # razem ze swoim assetem, bo bez niego kreacja nie ma sensu.
        all_names = sorted({cr["name"] for pl in proposal["placements"]
                            if not pl.get("serving")
                            for a in pl["ads"] for cr in a["creatives"]})
        creative_ids = {}
        for cname in all_names:
            cid_ = state["creatives_by_name"].get(cname)
            if cid_:
                self._rec("REUSE", "creative", cname, cid_)
            else:
                r = W.creative(self.svc, self.pid, self.adv, cname, dry_run=self.dry)
                cid_ = r.get("id", "(new)")
                self._rec("CREATE", "creative", cname, r.get("id"), "TRACKING_TEXT 1x1")
            # creative must be associated with the campaign before an ad can use it
            W.associate_creative_to_campaign(self.svc, self.pid, self.cid, cid_, dry_run=self.dry)
            self._rec("LINK", "campaign-creative", cname, cid_, f"-> campaign {self.cid}")
            creative_ids[cname] = cid_

        # 4) placements + ads (+ each ad's creatives)
        for pl in proposal["placements"]:
            if pl.get("serving"):
                self._run_serving(pl, site_of(pl), site_ids, lp_ids, proposal, state)
                continue
            site = site_of(pl)
            p_id = state["placements"].get((site, pl["name"]))
            if p_id:
                self._rec("REUSE", "placement", pl["name"], p_id, f"site={site}")
            else:
                r = W.placement(self.svc, self.pid, self.cid, site_ids[site], pl["name"],
                                self.start_date, self.end_date, dry_run=self.dry)
                p_id = r.get("id", "(new)")
                self._rec("CREATE", "placement", pl["name"], r.get("id"), f"site={site}")

            for a in pl["ads"]:
                ad_key = (site, pl["name"], a["name"])
                ad_id = state["ads"].get(ad_key)
                existing_cres = state["ad_creatives"].get(ad_key, set())
                cres = a["creatives"]

                if ad_id:
                    for cr in cres:
                        cname, cid_ = cr["name"], creative_ids[cr["name"]]
                        cr_lp_name, _ = self._lp_key(proposal, cr)
                        if cname in existing_cres:
                            self._rec("NO-OP", "ad", a["name"], ad_id, f"{cname} already assigned")
                        else:
                            W.append_creative_to_ad(self.svc, self.pid, ad_id, cid_, lp_ids[cr_lp_name],
                                                    dry_run=self.dry)
                            self._rec("UPDATE", "ad", a["name"], ad_id,
                                      f"append creative {cname} -> LP {cr_lp_name}")
                else:
                    first, rest = cres[0], cres[1:]
                    first_lp_name, _ = self._lp_key(proposal, first)
                    r = W.tracking_ad(self.svc, self.pid, self.cid, a["name"], p_id,
                                      creative_ids[first["name"]], lp_ids[first_lp_name],
                                      self.start_time, self.end_time, dry_run=self.dry)
                    ad_id = r.get("id", "(new)")
                    self._rec("CREATE", "ad", a["name"], r.get("id"),
                              f"placement={pl['name']} -> creative={first['name']} -> LP={first_lp_name}")
                    for cr in rest:
                        cname, cid_ = cr["name"], creative_ids[cr["name"]]
                        cr_lp_name, _ = self._lp_key(proposal, cr)
                        W.append_creative_to_ad(self.svc, self.pid, ad_id, cid_, lp_ids[cr_lp_name],
                                                dry_run=self.dry)
                        self._rec("UPDATE", "ad", a["name"], ad_id,
                                  f"append creative {cname} -> LP {cr_lp_name}")
        return self.log


def resolve_tag_pairs(state, proposal):
    """Map proposal['tags'] (name-based: site/placement/ad/creative) to live
    (adId, creativeId) pairs using freshly-fetched state (call after a real commit).
    Returns (pairs, missing) — missing entries couldn't be resolved yet (rare race)."""
    pairs, missing = [], []
    for t in proposal["tags"]:
        ad_id = state["ads"].get((t["site"], t["placement"], t["ad"]))
        cre_id = state["creatives_by_name"].get(t["creative"])
        if ad_id and cre_id:
            pairs.append((ad_id, cre_id))
        else:
            missing.append(t)
    return pairs, missing


def build_demo_proposal(svc, cid="36430023", dims=None):
    anchor = ["indywidualny", "ubezpieczenia"]
    all_lps = _fetch_campaign_lps(svc, TEST_PROFILE, TEST_ADVERTISER)
    camp_lps = [l for l in all_lps if l["campaignId"] == cid]
    url = ("https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/"
           "nieruchomosci/promocja/?utm_source=google&utm_medium=gdn")
    line = M.resolve_line(url, anchor, "GDN", camp_lps)
    parsed = parse_zip.parse(os.path.join(
        os.path.dirname(__file__), "..", "data", "samples", "GDN Citi.zip"))
    if dims:
        parsed["units"] = [u for u in parsed["units"] if u.get("dimension") in dims]
    camp = {"id": cid, "name": "Household 06-08.2026 - testy", "status": "existing"}
    return B.build_proposal("GDN", parsed, camp, line, campaign_lps=camp_lps, target_url=url), camp_lps


if __name__ == "__main__":
    execute = "--execute" in sys.argv
    dims = None
    if "--dims" in sys.argv:
        dims = set(sys.argv[sys.argv.index("--dims") + 1].split(","))
    cid = "36430023"
    svc = service(read_only=not execute)
    proposal, _ = build_demo_proposal(svc, cid, dims)
    campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
    state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
    orch = Orchestrator(svc, TEST_PROFILE, TEST_ADVERTISER, campaign, dry_run=not execute)
    orch.run(proposal, state)
    print("\n>> " + ("REAL WRITE done." if execute else "DRY-RUN only; nothing written."))
