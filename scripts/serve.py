"""Local backend (stdlib only, no npm) — serves the UI AND builds proposals.

  GET  /                     -> ui/index.html
  GET  /<file>               -> static file from ui/
  POST /api/build-proposal   -> {link|links[], keywords[]?, source, message,
                                 zipB64|zipPath, folderMap?} -> proposal JSON

Run:  py scripts/serve.py   (then open http://127.0.0.1:8765/)
Read-only against the TEST advertiser (cm_auth guard still applies).
"""
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import matcher as M
import build_proposal as B
import ai_fallback
import ai_agents as AG
from cm_auth import service
from cm_read import fetch_state, search_sites, existing_tree, site_structure, _paginate
import cm_env
from match_link import (_fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER,
                        MAP_PATH, advertiser_for)

UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui")


def friendly_error(e):
    """Turn an exception into something a trafficker can act on.

    A raw `ServerNotFoundError: Unable to find the server at dfareporting.googleapis.com`
    tells the user nothing about what to do; "sieć mrugnęła, kliknij ponownie" does. The
    original class name stays at the end so a bug report is still diagnosable.
    """
    name = type(e).__name__
    text = str(e)
    tail = f" [{name}]"

    # DNS / brak sieci / VPN — najczęstszy przypadek przy pracy na laptopie
    if name in ("ServerNotFoundError", "gaierror", "URLError") or "Unable to find the server" in text:
        return ("Brak połączenia z API Google. Sprawdź sieć/VPN i kliknij ponownie — "
                "propozycja nie została zbudowana, więc nic się nie zapisało." + tail)
    if name in ("TimeoutError", "socket.timeout", "timeout") or "timed out" in text.lower():
        return ("API Google nie odpowiedziało w czasie. Spróbuj ponownie — jeśli powtarza "
                "się, to zwykle chwilowe spowolnienie po stronie Google." + tail)
    if name in ("SSLError", "SSLEOFError", "CertificateError"):
        return ("Błąd TLS przy połączeniu z Google — zwykle firmowy proxy/antywirus "
                "podmieniający certyfikat." + tail)
    # token OAuth
    if name == "RefreshError" or "invalid_grant" in text or "Token has been expired" in text:
        return ("Token OAuth wygasł lub został unieważniony. Uruchom "
                "`py scripts/cm_auth.py`, żeby zalogować się ponownie." + tail)
    # bezpiecznik allowlisty ma już czytelny komunikat — nie zaciemniaj go
    if name == "RuntimeError" and "SAFETY guard" in text:
        return text
    if name == "HttpError":
        status = getattr(getattr(e, "resp", None), "status", "?")
        return f"CM360 odrzuciło żądanie (HTTP {status}): {text}"
    return f"{name}: {text}"


CTYPE = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".css": "text/css", ".json": "application/json",
         # bez tego SVG leci jako octet-stream i część przeglądarek go nie narysuje
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


def _lp_folder_candidates(parsed, selected=None):
    """Top-level zip folders that could denote a landing page. Both parse_zip buckets
    count: `remarketing/` is a GROUP_KEYWORD so it lands in `groups`, `prospecting/`
    is not so it lands in `variants` — for LP matching the distinction is meaningless.

    A folder of a SELECTED SOURCE is excluded: it says which source the materials are
    for, never which page. Without this the `GDN/` + `Programmatic/` package broke —
    the addresses carried `utm_source=gdn` / `=programmatic`, so both folders matched a
    landing page by name, were consumed as LP discriminators, stopped being source
    groups and the second source vanished from the tree without a word.
    """
    src_folders = {g["name"] for g in parsed.get("groups") or []
                   if B.group_source(g, selected or [])}
    src_folders |= {v for v in parsed.get("variants") or []
                    if B.group_source({"name": v}, selected or [])}
    names = [g["name"] for g in parsed.get("groups") or [] if g["name"] not in src_folders]
    return names + [v for v in parsed.get("variants") or []
                    if v and v not in names and v not in src_folders]


def _lp_package_candidates(parsed):
    """Nazwy PACZEK, które mogą wskazywać stronę docelową (obok folderów w środku zipa).

    Dostawca coraz częściej dzieli materiał nie folderem, tylko osobnym plikiem na linię.
    Taka paczka ma jeden folder opakowujący, który parser obcina, więc kandydatów nie było
    wcale — a przy dwóch liniach brak przypisania znaczy „wszystko do obu", czyli każdy ad
    dostawał obie kreacje. Zgłoszone na realnym zleceniu 17.09.2026.

    Kandydatem jest paczka, która ma RODZEŃSTWO W TYM SAMYM ŹRÓDLE i nie dzieli materiału
    folderami w środku:
    * paczki rozdzielone ŹRÓDŁAMI (`..._gdn.zip` + `..._programmatic.zip`) mówią o źródle,
      nie o stronie — ta sama zasada, przez którą folder wybranego źródła nie jest
      kandydatem (inaczej `GDN/` + `Programmatic/` zostały zjedzone jako rozróżnienie LP
      i drugie źródło znikało z drzewa bez słowa). Grupujemy więc po źródle, zamiast
      odrzucać każdą paczkę, która jakieś ma: przy kilku paczkach KAŻDA dostaje źródło
      zlecenia, więc odrzucanie „po posiadaniu źródła" wykluczało wszystkie;
    * jedna paczka w źródle to całe jego zlecenie, nie jego część — nie ma czego rozróżniać;
    * paczka dzieląca materiał FOLDERAMI ma już dokładniejsze rozróżnienie niż nazwa pliku.
      Grupa równa nazwie źródła się nie liczy — to znacznik doklejony przy scalaniu paczek,
      a nie folder od dostawcy.
    """
    packs = parsed.get("packages") or []
    if len(packs) < 2:
        return []
    units = parsed.get("units") or []
    by_source = {}
    for p in packs:
        by_source.setdefault(str(p.get("source") or ""), []).append(p)
    out = []
    for src, group in by_source.items():
        if len(group) < 2:
            continue
        for p in group:
            mine = [u for u in units if u.get("_zipName") == (p.get("name") or "")]
            inner = [f for f in (B._unit_folder(u) for u in mine)
                     if f and str(f).lower() != src.lower()]
            label = B.pkg_label(p.get("name"))
            if mine and not inner and label:
                out.append(label)
    return out


def _match_lp_folders(links, anchor, parsed, override=None, keywords=None, selected=None,
                      message=""):
    """Deterministic folder -> landing page matching, with the user's answers on top.

    Returns (folder_match, labels) where labels feeds resolve_lines: a folder name is
    the fallback label for a landing page whose URL carries no readable discriminator.

    A keyword the user typed for a landing page joins its discriminator tokens: the
    folder `Lookalike/` and the keyword `lookalike` are exactly the pairing this is
    meant to find, and a keyword is a better signal than anything derived from the URL.
    """
    discs = M.lp_discriminators(links, anchor)
    for i, kw in (keywords or {}).items():
        tok = M.normalize(M.keyword_label(kw) or "")
        if tok and int(i) < len(discs) and tok not in discs[int(i)]:
            discs[int(i)] = [tok] + discs[int(i)]
    packs = _lp_package_candidates(parsed)
    fm = M.match_folders_to_lps(_lp_folder_candidates(parsed, selected) + packs, discs)
    # Paczki są kandydatami innego rodzaju niż foldery i muszą być rozpoznawalne dalej:
    # o nieprzypisaną paczkę PYTAMY zawsze (patrz `unresolved_lp_folders`), a jej nazwa
    # nie może zostać etykietą strony docelowej — mówi, KTÓRA strona dostaje materiał,
    # a nie jak się nazywa (`linia4-FB-BC-Meta-Ads` byłoby bez sensu).
    fm["packages"] = packs
    # Przypisanie paczki do linii podane WPROST w zleceniu bije dopasowanie po nazwie:
    # to zdanie napisał człowiek o tej konkretnej dostawie, a nazwa pliku rzadko
    # przypomina nazwę strony. Nadal ustępuje odpowiedzi z UI (niżej).
    from_msg = B.packages_from_message(message, packs,
                                       [(keywords or {}).get(i) or (keywords or {}).get(str(i))
                                        for i in range(len(links))]
                                       if isinstance(keywords, dict) else list(keywords or []))
    if from_msg:
        fm = dict(fm, map=dict(fm.get("map") or {}, **from_msg),
                  unmatched=[f for f in fm.get("unmatched") or [] if f not in from_msg],
                  ambiguous=[a for a in fm.get("ambiguous") or []
                             if a["folder"] not in from_msg],
                  fromMessage=dict(from_msg))
    # Only folders the AUTOMATIC pass recognised by name stop being placement
    # discriminators — those are landing-page folders and nothing else. A user answer
    # says which page a folder feeds; it does NOT stop `screening/` from being a format
    # folder of its own, so it must not consume it.
    fm["consumed"] = sorted(fm.get("map") or {})
    if override:
        fm = dict(fm, map=dict(fm.get("map") or {}),
                  ambiguous=[a for a in fm.get("ambiguous") or []
                             if a["folder"] not in override],
                  unmatched=[f for f in fm.get("unmatched") or []
                             if f not in override],
                  # echoed back so the UI keeps no hidden state of its own: answering a
                  # second folder resends the first answer along with it
                  override=dict(override))
        for folder, val in override.items():
            if str(val).isdigit():
                fm["map"][folder] = int(val)
            else:
                fm["map"].pop(folder, None)          # "all" -> feeds every line
    labels = {}
    for folder, idx in (fm.get("map") or {}).items():
        if folder not in packs:
            labels.setdefault(idx, folder)
    return fm, labels


def _line_entries(links, keywords, labels, selected, row_sources, source_map=None):
    """One landing page per (address × source) — the shape `resolve_lines` needs.

    The source is part of an LP name, so an order covering several sources needs an LP
    per source: `linia1-GDN` next to `linia1-Programmatic`, same number, normally the
    same page with different tracking parameters. An address the user assigned to a
    source belongs to that source; a source with no address of its own reuses the
    addresses of the PRIMARY source (that is the "same page, two sources" case).

    Returns (urls, {i: labelFallback}, {i: keyword}, {i: sourceToken}, [addressIndex]).
    """
    urls, ent_labels, ent_kw, ent_src, addr_of = [], {}, {}, {}, []
    for s in selected:
        own = [i for i in range(len(links)) if (row_sources or {}).get(i) == s]
        if not own:
            own = [i for i in range(len(links))
                   if (row_sources or {}).get(i, selected[0]) == selected[0]]
        for i in own:
            j = len(urls)
            urls.append(links[i])
            ent_src[j] = B.lp_source(s, source_map)
            if i in labels:
                ent_labels[j] = labels[i]
            if i in keywords:
                ent_kw[j] = keywords[i]
            addr_of.append(i)
    return urls, ent_labels, ent_kw, ent_src, addr_of


def _parse_packages(packs, source, selected):
    """Sparsuj i SCAL wszystkie paczki zlecenia; zwróć (parsed, ścieżka pierwszej paczki).

    Źródło paczki: wskazane przez użytkownika, a gdy nie wskazał — odczytane z NAZWY
    pliku (`household_gdn.zip` -> GDN), o ile to źródło jest w zleceniu. Nazwa pliku jest
    tu wiarygodnym sygnałem, bo dostawcy tak właśnie rozdzielają paczki per źródło; gdy
    nic z niej nie wynika i paczek jest kilka, przypisujemy źródło GŁÓWNE.
    """
    parts = []
    for i, p in enumerate(packs):
        parsed = parse_zip.parse(p["path"])
        src = p.get("source")
        if src not in selected:
            hint = parse_zip._source_hint([p.get("name") or ""])
            src = hint if hint in selected else (source if len(packs) > 1 else None)
        parts.append({"parsed": parsed, "source": src, "path": p["path"],
                      "name": p.get("name")})
    return parse_zip.merge_parsed(parts), parts[0]["path"]


def build_proposal(link, zip_path, source, message="", campaign_id=None, new_campaign=None,
                   links=None, folder_map=None, keywords=None, sources=None,
                   row_sources=None, row_audiences=None, mail_links=None,
                   advertiser_id=None):
    """Build the editable proposal for one order.

    `links` carries SEVERAL landing pages that all belong to the same campaign; `link`
    is the single-link shorthand and stays the primary one. Every link must resolve to
    the same advertiser — a mixed order is a mistake worth refusing rather than
    trafficking half of.

    `keywords` is the word the user typed next to each address, positionally aligned
    with `links`; it becomes that landing page's label (`linia3-FB-lookalike`).

    `sources` are ALL sources of this order (primary `source` first) — a package that
    separates sources by folder (`GDN/` + `Programmatic/`) is trafficked in one go.
    `row_sources` says which source each address belongs to ({addressIndex: source}).
    """
    rules = json.load(open(MAP_PATH, encoding="utf-8"))["rules"]
    # deduplicate before anything is keyed by position (see matcher.dedupe_links)
    links, keywords, row_sources = M.dedupe_links(links or [link], keywords, row_sources)
    if not links:
        return {"error": "Podaj co najmniej jeden link do strony docelowej."}
    link = links[0]
    resolved = [(l, M.resolve_advertiser(l, rules)) for l in links]
    if not resolved[0][1]:
        return {"error": "Żadna reguła nie dopasowała advertisera (tu wejdzie fallback AI)."}
    rule = resolved[0][1]
    # Kilku advertiserów pasuje do tego samego adresu (zagnieżdżone anchory). Długość
    # anchora rozstrzyga to technicznie, ale produkcja pokazała, że merytorycznie nie ma
    # tu jednej odpowiedzi — patrz `matcher.advertiser_candidates`. Pytamy więc człowieka
    # zamiast wybierać po cichu. Na koncie testowym pytanie jest bez sensu: tam i tak
    # wygrywa jedyny advertiser testowy, cokolwiek powie link.
    cands = M.advertiser_candidates(link, rules)
    if advertiser_id:
        picked = next((r for r in cands if str(r.get("advertiserId")) == str(advertiser_id)),
                      None)
        if not picked:
            return {"error": f"Advertiser {advertiser_id} nie pasuje do tego adresu."}
        rule = picked
    elif len(cands) > 1 and TEST_ADVERTISER is None:
        return {"advertiserChoice": {
            "link": link,
            "reason": "Ten adres pasuje do kilku advertiserów i na koncie klienta obie "
                      "wersje realnie występują — wskaż, do którego trafficujemy.",
            "candidates": [{"advertiserId": str(r.get("advertiserId")),
                            "advertiser": r.get("advertiser"),
                            "anchor": r.get("anchor") or [],
                            "note": r.get("_verified") or r.get("_note") or ""}
                           for r in cands]}}
    # Zgodność pozostałych adresów sprawdzamy po KANDYDATACH, nie po zwycięzcy domyślnym:
    # gdy człowiek wybrał krótszy anchor (Konta zamiast Intensive), `resolve_advertiser`
    # dalej wskazuje ten dłuższy i bez tego pierwszy link zostałby zgłoszony jako obcy.
    want = str(rule.get("advertiserId"))
    bad = [l for l in links
           if want not in {str(r.get("advertiserId")) for r in M.advertiser_candidates(l, rules)}]
    if bad:
        return {"error": "Linki wskazują różnych advertiserów — jedno zlecenie musi "
                         f"dotyczyć jednego. Nie pasuje do „{rule.get('advertiser')}”: "
                         + ", ".join(bad)}
    # Advertiser, na którym realnie pracujemy. Na koncie testowym jest jeden i wygrywa
    # zawsze (o to chodzi w trybie testowym: produkcyjne adresy mBanku przepuszczamy
    # przez advertisera testowego). Na produkcji bierze go LINK — dlatego wszystko niżej
    # używa `adv_id`, a nie stałej.
    try:
        adv_id = advertiser_for(rule)
    except RuntimeError as e:
        return {"error": str(e)}
    anchor = rule.get("anchor", [])
    svc = service(read_only=True)
    # `zip_path` przyjmuje albo jedną ścieżkę (jak dotąd), albo listę paczek zlecenia
    packs = (zip_path if isinstance(zip_path, list)
             else [{"path": zip_path, "name": os.path.basename(zip_path), "source": None}])
    parsed, zip_path = _parse_packages(packs, source,
                                       B.selected_sources(source, sources))
    # `source` stays the config key (Site, placement names, adKey); only the LP/creative
    # names use the short form the account already holds (Facebook -> linia3-FB)
    lp_src = B.lp_source(source)
    selected = B.selected_sources(source, sources)
    folder_match, labels = _match_lp_folders(links, anchor, parsed, folder_map, keywords,
                                            selected, message)
    # źródło przypisane do adresu ma sens tylko jeśli jest wśród wybranych
    row_src = {int(i): s for i, s in (row_sources or {}).items() if s in selected}
    # programmatic: etykietą LP jest AUDIENCJA, a słowo klucza staje się nazwą linii
    aud_labels, line_label = B.serving_line_labels(links, keywords, source,
                                                  row_audiences, row_src)
    if aud_labels is not None:
        keywords = aud_labels
    ent_urls, ent_labels, ent_kw, ent_src, addr_of = _line_entries(
        links, keywords, labels, selected, row_src)

    if new_campaign:
        # brand-new campaign: no campaign LPs yet, so these links are the first lines
        # and every placement/ad/creative below is new. Account-level sites still exist,
        # so pass them as an empty-placement tree to keep the Site badge honest.
        state = fetch_state(svc, TEST_PROFILE, adv_id)
        prop = B.build_proposal(source, parsed,
                                {"id": None, "name": new_campaign, "status": "new"},
                                lines=M.resolve_lines(ent_urls, anchor, lp_src, [],
                                                      ent_labels, ent_kw, ent_src),
                                existing={s: {} for s in state["sites_by_name"]},
                                campaign_lps=[], target_url=link, message=message,
                                folder_match=folder_match, sources=selected,
                                line_addresses=addr_of, line_label=line_label)
        return _attach_ai(prop, parsed, message, rules, adv_id)

    camp_lps = _fetch_campaign_lps(svc, TEST_PROFILE, adv_id)
    matched_by = None
    if campaign_id:
        # explicit override (user manually picked a campaign from the browse list)
        cid = campaign_id
        matched_by = {"why": "wybrana ręcznie"}
    else:
        # all links share one campaign; the first that matches an existing one decides,
        # so a brand-new path next to a known one doesn't force a new campaign
        ranked_first, cid = [], None
        for l in links:
            ranked, suggest_new = M.match_campaigns(l, anchor, camp_lps)
            ranked_first = ranked_first or ranked
            if not suggest_new:
                cid = ranked[0]["campaignId"]
                # PO KTÓRYM adresie i DLACZEGO kampania się dopasowała — przy kilku
                # adresach w zleceniu decyduje pierwszy, który trafił, i user ma prawo
                # to wiedzieć bez czytania kodu (punkt 11 kolejki)
                matched_by = {"why": ranked[0]["why"], "link": l,
                              "lpName": ranked[0].get("lpName"),
                              "lpUrl": ranked[0].get("lpUrl")}
                break
        if not cid:
            return {"suggestNewCampaign": True, "advertiser": rule.get("advertiser"),
                    "message": "Brak kampanii z pasującą ścieżką — zasugerowano utworzenie nowej.",
                    "pathHint": "/".join(M.remaining_path(link, anchor) or []),
                    "candidates": ranked_first[:5]}

    this = [l for l in camp_lps if l["campaignId"] == cid]
    # MAILING: strony docelowe nie pochodzą z formularza, tylko z linków w `index.html`.
    # Adres wpisany w formularzu służy tu wyłącznie do dopasowania advertisera i kampanii.
    if (B.source_conf(source) or {}).get("mailing") and parsed.get("mailings"):
        campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
        camp_node = {"id": cid, "name": campaign["name"], "status": "existing",
                     "matchedBy": matched_by}
        prop = B.build_proposal(
            source, parsed, camp_node,
            lines=B.mailing_lines(parsed, B.source_conf(source), camp_node,
                                  start_no=M.next_mail_number(this),
                                  override=mail_links, main_url=link),
            existing=existing_tree(fetch_state(svc, TEST_PROFILE, adv_id, cid)),
            campaign_lps=this, target_url=link, sources=selected, message=message)
        return _attach_ai(prop, parsed, message, rules, adv_id)
    lines = M.resolve_lines(ent_urls, anchor, lp_src, this, ent_labels, ent_kw, ent_src)
    # the reuse-vs-new-line question is per landing page AND per source (the source is
    # part of the LP name, so `linia2-GDN` and `linia2-Programmatic` collide separately)
    conflict = next((c for c in (M.detect_line_conflict(u, anchor, ent_src[j], this)
                                 for j, u in enumerate(ent_urls)) if c["conflict"]),
                    {"conflict": False})
    campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
    state = fetch_state(svc, TEST_PROFILE, adv_id, cid)
    prop = B.build_proposal(source, parsed,
                            {"id": cid, "name": campaign["name"], "status": "existing",
                             "matchedBy": matched_by},
                            lines=lines, existing=existing_tree(state), campaign_lps=this,
                            target_url=link, line_conflict=conflict,
                            folder_match=folder_match, sources=selected,
                            line_addresses=addr_of, line_label=line_label,
                            message=message)
    return _attach_ai(prop, parsed, message, rules, adv_id)


def _attach_ai(proposal, parsed, message, rules, advertiser_id=None):
    """Attach the escalation points and the ready-made agent (a) request.

    Dokłada też KONTO, którego ta propozycja dotyczy (`advertiserId` + `env`). Bez tego
    `/api/commit` musiałby zgadywać, do kogo pisać — na koncie testowym advertiser jest
    jeden, ale na produkcji wybiera go link, a propozycja jedzie do przeglądarki i wraca,
    więc musi nieść tę informację ze sobą.

    Carrying the request in the proposal means /api/assist needs no re-upload and no
    re-parse of the zip — the client just hands back what it already has. Escalations
    are informational: an empty list means the deterministic rules were enough and no
    model is needed for this order.
    """
    advertisers = sorted({r.get("advertiser") for r in rules if r.get("advertiser")})
    proposal["account"] = dict(cm_env.describe(), advertiserId=advertiser_id)
    proposal["ai"] = {
        "escalations": ai_fallback.escalations(parsed, proposal, message),
        "request": ai_fallback.build_request(parsed, proposal, message, advertisers),
        "wired": {"structure": AG.configured("N8N_STRUCTURE_URL"),
                  "intent": AG.configured("N8N_INTENT_URL")},
    }
    return proposal


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/api/sites":
            from urllib.parse import urlparse, parse_qs
            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
            try:
                res = search_sites(service(read_only=True), TEST_PROFILE, q)
                return self._send(200, json.dumps(res, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
        if route == "/api/site-structure":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            cid = (qs.get("campaignId") or [""])[0]
            site = (qs.get("site") or [""])[0]
            # advertiser podaje klient (z propozycji); na koncie testowym jest jeden,
            # więc stała zostaje fallbackiem i stare wywołania działają bez zmian
            adv = (qs.get("advertiserId") or [""])[0] or TEST_ADVERTISER
            if not cid or not site:
                return self._send(400, json.dumps({"error": "wymagane: campaignId, site"}))
            if not adv:
                return self._send(400, json.dumps({"error": "wymagane: advertiserId "
                                                            "(na produkcji nie ma domyslnego)"}))
            try:
                svc = service(read_only=True)
                state = fetch_state(svc, TEST_PROFILE, adv, cid)
                return self._send(200, json.dumps(site_structure(state, site), ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
        if route == "/api/env":
            # Które konto jest pod spodem — UI musi to pokazać ZANIM user cokolwiek
            # kliknie. Bez sieci i bez tokenu: to czysty odczyt configu.
            return self._send(200, json.dumps(cm_env.describe(), ensure_ascii=False))
        if route == "/api/campaigns":
            try:
                svc = service(read_only=True)
                from urllib.parse import urlparse as _u, parse_qs as _q
                adv = (_q(_u(self.path).query).get("advertiserId") or [""])[0] or TEST_ADVERTISER
                if not adv:
                    return self._send(400, json.dumps(
                        {"error": "wymagane: advertiserId (na produkcji nie ma domyslnego)"}))
                camps = _paginate(svc.campaigns, "campaigns", profileId=TEST_PROFILE,
                                  advertiserIds=[adv], sortField="NAME")
                out = [{"id": c["id"], "name": c["name"]} for c in camps]
                return self._send(200, json.dumps({"campaigns": out}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
        if route == "/api/tags-file":
            # Pobranie wygenerowanego arkusza. Wpuszczamy WYŁĄCZNIE nazwę pliku z
            # katalogu data/ — `basename` ucina każdą próbę wyjścia w górę drzewa
            # (../../credentials/token.json), a dodatkowo wymagamy rozszerzenia .xls.
            from urllib.parse import urlparse, parse_qs, quote
            name = os.path.basename((parse_qs(urlparse(self.path).query).get("name") or [""])[0])
            data_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
            path = os.path.normpath(os.path.join(data_dir, name))
            if not name.lower().endswith(".xls") or not path.startswith(data_dir) \
                    or not os.path.isfile(path):
                return self._send(404, json.dumps({"error": f"Nie ma pliku {name!r}."}),
                                  "application/json; charset=utf-8")
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.ms-excel")
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quote(name)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if route == "/api/campaign-lps":
            from urllib.parse import urlparse, parse_qs
            cid = (parse_qs(urlparse(self.path).query).get("campaignId") or [""])[0]
            if not cid:
                return self._send(400, json.dumps({"error": "wymagane: campaignId"}))
            try:
                svc = service(read_only=True)
                lps = _paginate(svc.advertiserLandingPages, "landingPages",
                                profileId=TEST_PROFILE, campaignIds=[cid])
                out = sorted([{"name": lp.get("name"), "url": lp.get("url", "")} for lp in lps],
                            key=lambda x: x["name"] or "")
                return self._send(200, json.dumps({"landingPages": out}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
        rel = route.lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(UI_DIR, rel))
        if not path.startswith(os.path.normpath(UI_DIR)) or not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        with open(path, "rb") as f:
            self._send(200, f.read(), CTYPE.get(os.path.splitext(path)[1], "application/octet-stream"))

    def do_POST(self):
        route = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if route == "/api/build-proposal":
                return self._send(200, json.dumps(self._build(req), ensure_ascii=False))
            if route == "/api/refine":
                return self._send(200, json.dumps(self._refine(req), ensure_ascii=False))
            if route == "/api/assist":
                return self._send(200, json.dumps(self._assist(req), ensure_ascii=False))
            if route == "/api/apply-suggestions":
                return self._send(200, json.dumps(self._apply_suggestions(req),
                                                  ensure_ascii=False))
            if route == "/api/promote":
                return self._send(200, json.dumps(self._promote(req), ensure_ascii=False))
            if route == "/api/commit":
                return self._send(200, json.dumps(self._commit(req), ensure_ascii=False))
            if route == "/api/create-site":
                return self._send(200, json.dumps(self._create_site(req), ensure_ascii=False))
            return self._send(404, json.dumps({"error": "unknown endpoint"}))
        except Exception as e:
            self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))

    def _build(self, req):
        # KILKA PACZEK w jednym zleceniu: `zips: [{name, b64, source?}]`. Stare pola
        # (`zipB64`/`zipName`/`zipPath`) nadal działają — to ta sama ścieżka z jedną paczką.
        zips = list(req.get("zips") or [])
        if not zips and (req.get("zipB64") or req.get("zipPath")):
            zips = [{"name": req.get("zipName"), "b64": req.get("zipB64"),
                     "path": req.get("zipPath"), "source": None}]
        packs = []
        for i, z in enumerate(zips):
            path = z.get("path")
            if z.get("b64"):
                safe = os.path.basename(z.get("name") or f"up{i}.zip")
                path = os.path.join(tempfile.gettempdir(), f"cmw{i}_{safe}")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(z["b64"].split(",")[-1]))
            if path:
                packs.append({"path": path, "name": z.get("name") or os.path.basename(path),
                              "source": z.get("source") or None})
        links = req.get("links") or ([req["link"]] if req.get("link") else [])
        if not links or not packs or not req.get("source"):
            return {"error": "wymagane: link(i), co najmniej jedna paczka .zip, source"}
        return build_proposal(links[0], packs, req["source"], req.get("message", ""),
                              campaign_id=req.get("campaignId"),
                              new_campaign=req.get("newCampaign"),
                              links=links, folder_map=req.get("folderMap"),
                              keywords=req.get("keywords"), sources=req.get("sources"),
                              row_sources=req.get("linkSources"),
                              row_audiences=req.get("linkAudiences"),
                              mail_links=req.get("mailLinks"),
                              advertiser_id=req.get("advertiserId"))

    def _create_site(self, req):
        """Add a Site to the account. dryRun=True -> plan only (still resolves the
        Site Directory entry, which is a read); dryRun=False -> real insert."""
        import cm_write as W
        name = (req.get("name") or "").strip()
        if not name:
            return {"error": "Podaj nazwę Site."}
        dry = req.get("dryRun", True)
        try:
            r = W.create_site(service(read_only=dry), TEST_PROFILE, name, req.get("url"),
                              req.get("directorySiteId"),
                              allow_new_directory_site=bool(req.get("allowNewDirectorySite")),
                              dry_run=dry)
        except Exception as e:
            return {"error": friendly_error(e)}
        out = {"dryRun": dry, "name": name,
               "directorySiteId": r.get("directorySiteId") or r.get("_directorySiteId"),
               "resolution": r.get("resolution"),
               "needsNewDirectorySite": r.get("needsNewDirectorySite", False)}
        if not dry:
            out["siteId"] = r.get("id")
            out["createdDirectorySite"] = r.get("_createdDirectorySite", False)
        return out

    def _promote(self, req):
        """Zatwierdzone decyzje AI -> trwałe reguły w configu.

        Dwustopniowo, jak tworzenie Site: bez `apply` zwracamy sam DIFF do obejrzenia,
        dopiero `apply: true` zapisuje (z kopią `.bak`). Powód jest ten sam co przy
        zapisach do CM360 — config działa potem bez nadzoru, na wszystkich kolejnych
        zleceniach, więc nie ma tu miejsca na „kliknąłem i się zapisało”.

        Zmiany do zapisu bierzemy z WŁASNEGO wyliczenia na podstawie sugestii, nie z tego,
        co przyśle klient: inaczej przeglądarka mogłaby wpisać do configu cokolwiek,
        omijając progi pewności i blokady.
        """
        import promote
        suggestions = req.get("suggestions") or {}
        adv_id = (req.get("proposal") or {}).get("account", {}).get("advertiserId")
        src_map, adv_map = promote.load()
        diff = promote.changes(suggestions, src_map, adv_map, advertiser_id=adv_id)
        if not req.get("apply"):
            return {"changes": diff, "applied": False,
                    "note": "podgląd — nic nie zapisano"}
        approve = set(req.get("approve") or [])
        chosen = [c for c in diff if not c.get("blocked")
                  and (not approve or c["path"] in approve)]
        if not chosen:
            return {"changes": diff, "applied": False,
                    "note": "nie ma czego zapisać (nic nie zatwierdzono albo wszystko zablokowane)"}
        new_src, new_adv, log = promote.apply_changes(chosen, src_map, adv_map)
        written = promote.save(new_src, new_adv)
        return {"changes": diff, "applied": True, "log": log,
                "files": [os.path.basename(w) for w in written],
                "note": "zapisano; kopie poprzednich wersji leżą obok jako .bak"}

    def _commit(self, req):
        """Run the orchestrator on the (effective) proposal. dryRun=True -> read-only
        plan; dryRun=False -> real writes (scoped guard) + tag .xls delta."""
        from orchestrate import Orchestrator, resolve_tag_pairs
        import export_tags
        proposal = req.get("proposal") or {}
        dry = req.get("dryRun", True)
        camp_spec = proposal.get("campaign") or {}
        cid = camp_spec.get("id")
        is_new = camp_spec.get("status") == "new"
        if not cid and not is_new:
            return {"error": "Brak campaign.id — najpierw zbuduj propozycję."}
        if is_new and not (camp_spec.get("name") or "").strip():
            return {"error": "Nowa kampania wymaga nazwy."}
        # Advertiser jedzie w propozycji (`account.advertiserId`) — to jedyne miejsce,
        # ktore wie, z jakiego LINKU powstala. Stala zostaje fallbackiem dla konta
        # testowego i dla propozycji zbudowanych, zanim to pole istnialo.
        adv = (proposal.get("account") or {}).get("advertiserId") or TEST_ADVERTISER
        if not adv:
            return {"error": "Propozycja nie niesie advertisera, a na produkcji nie ma "
                             "domyslnego — zbuduj ja ponownie."}
        svc = service(read_only=dry)
        if is_new:
            # nothing to read yet — the orchestrator creates it and assigns dates/id
            campaign, state = camp_spec, fetch_state(svc, TEST_PROFILE, adv)
        else:
            campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
            state = fetch_state(svc, TEST_PROFILE, adv, cid)
        # PLACEMENTY SERWUJĄCE (programmatic) — writer przeszedł pełny przebieg na żywym
        # koncie testowym 15.09.2026 (kampania 36889536): upload assetu -> kreacja DISPLAY
        # -> placement z wymiarem -> ad `AD_SERVING_STANDARD_AD`, a CM sam dołożył ad
        # `300x250 Default Web Ad` biorący domyślną stronę kampanii. Bramka blokująca
        # realny zapis została zdjęta; ostrzeżenie zostaje, bo ta ścieżka wgrywa MATERIAŁY
        # i jest nieodwracalna (CM360 nie ma DELETE dla kreacji ani assetów).
        serving = Orchestrator.serving_names(proposal)
        if not dry:
            # Sites must already exist before we write anything: site creation sits AFTER
            # the LP/campaign steps, so failing there would leave a half-written campaign.
            wanted = {(proposal.get("site") or {}).get("name")} | {
                pl.get("site") for pl in proposal.get("placements", [])}
            missing = sorted(n for n in wanted if n and n not in state["sites_by_name"])
            if missing:
                return {"error": f"Site nie istnieje na koncie: {', '.join(missing)}. "
                                 f"Dodaj go najpierw („Szukaj/dodaj site” w formularzu), "
                                 f"żeby zapis nie przerwał się po utworzeniu LP/kampanii."}
        # LP bez adresu z tego samego powodu: CM360 odrzuca insert bez url (18112),
        # ale robi to dopiero w środku zapisu, gdy kampania i część LP już powstały.
        no_url = Orchestrator.lp_urls_missing(proposal, state)
        if no_url and not dry:
            def _gdzie(w):     # przy LP linii dotyczy to wszystkich creative — nie wypisuj setek
                return ", ".join(w[:3]) + (f" i {len(w) - 3} więcej" if len(w) > 3 else "")
            szczegoly = "; ".join(f"{n} (użyte w: {_gdzie(w)})" for n, w in sorted(no_url.items()))
            return {"error": "Nie zapisuję: te strony docelowe nie mają adresu URL, a nie "
                             "istnieją jeszcze w kampanii — CM360 odrzuciłby je w połowie "
                             f"zapisu (błąd 18112). Uzupełnij adres albo wskaż istniejące "
                             f"LP. Brakuje: {szczegoly}"}
        # DRUGA strona docelowa na adres, który w kampanii już swoją ma. Bywa zamierzona
        # („dodaj jako nową linię"), ale LP w CM360 się NIE USUWA, więc pomyłka zostaje
        # na koncie klienta na zawsze. Pytamy raz, wprost, przed realnym zapisem — dry-run
        # pokazuje to samo jako ostrzeżenie, żeby nie było zaskoczeniem przy kliknięciu.
        dup = Orchestrator.duplicate_lp_urls(proposal, state)
        if dup and not dry and not req.get("confirmDuplicateLp"):
            return {"needsConfirm": {
                "kind": "duplicateLp",
                "items": [{"lpName": n, "existing": e} for n, e in sorted(dup.items())],
                "message": "Powstaną nowe strony docelowe na adresach, które w tej kampanii "
                           "już mają swoją stronę. W CM360 strony docelowej NIE DA SIĘ "
                           "usunąć, więc to jest nieodwracalne. Potwierdź, jeśli o to chodzi."}}
        orch = Orchestrator(svc, TEST_PROFILE, adv, campaign, dry_run=dry)
        log = orch.run(proposal, state)
        out = {"dryRun": dry, "log": log, "campaignId": orch.cid}
        if dup:
            out["duplicateLpWarning"] = [{"lpName": n, "existing": e}
                                         for n, e in sorted(dup.items())]
        if no_url:
            # w dry-runie tylko ostrzegamy, żeby użytkownik zobaczył problem PRZED
            # kliknięciem zapisu, a nie dopiero jako odmowę
            out["lpUrlWarning"] = sorted(no_url)
        if serving:
            out["servingWarning"] = serving
        if not dry:
            cid = orch.cid                 # a brand-new campaign only has an id now
            # recompute tags fresh from the (possibly user-edited) placements — the
            # client may have added placements/ads/creatives after the proposal was
            # first built, so proposal["tags"] as received can be stale.
            proposal["tags"] = B.compute_tags(proposal)
            # refetch fresh state (everything just written now exists) and resolve
            # the proposal's (site/placement/ad/creative) tag rows to live ids
            fresh = fetch_state(svc, TEST_PROFILE, adv, cid)
            pairs, missing = resolve_tag_pairs(fresh, proposal)
            if pairs:
                # nazwa pliku powstaje PO odczycie, bo schemat wymaga nazw kampanii
                # i advertisera, a nie ich id: Tags_kampania_advertiser_data.xls
                camp, adv, rows = export_tags.collect_rows(svc, TEST_PROFILE, cid, pairs)
                path = os.path.join(os.path.dirname(__file__), "..", "data",
                                    export_tags.tags_filename(camp, adv))
                export_tags.write_xls(camp, adv, rows, path)
                out["tags"] = {"file": os.path.abspath(path),
                               "name": os.path.basename(path), "count": len(rows),
                               "rows": [{"ad": r[13], "creative": r[15]} for r in rows]}
            if missing:
                out["tagsWarning"] = f"nie rozwiązano {len(missing)} tagów: {missing[:3]}"
        return out

    def _refine(self, req):
        """Agent (b): interpret the user's remarks about a structure they don't like.

        n8n returns EDIT OPERATIONS; this applies them deterministically so the tree is
        never whatever the model happened to echo back. The op log is the diff the user
        reviews, and `unclear` carries anything the agent refused to guess."""
        proposal, remarks = req.get("proposal", {}), (req.get("remarks") or "").strip()
        if not proposal.get("placements"):
            return {"error": "Brak struktury do poprawienia."}
        if not remarks:
            return {"error": "Napisz, co poprawić."}
        if not AG.configured("N8N_INTENT_URL"):
            return {"error": "Agent (b) nie jest podpięty — ustaw N8N_INTENT_URL "
                             "na adres webhooka n8n i zrestartuj serve.py."}
        ai_req = AG.build_intent_request(proposal, remarks, req.get("answers"))
        try:
            result = ai_fallback.interpret(ai_req, call=AG.intent_call())
        except AG.AgentError as e:
            return {"error": str(e), "aiRequest": ai_req}
        new_proposal, log = AG.apply_ops(proposal, result.get("ops"))
        new_proposal["tags"] = B.compute_tags(new_proposal)
        applied = [e for e in log if e["ok"]]
        return {"proposal": new_proposal, "log": log,
                "applied": len(applied), "skipped": len(log) - len(applied),
                "unclear": result.get("unclear") or [],
                "confidence": result.get("confidence"),
                "notes": result.get("notes") or ""}

    def _apply_suggestions(self, req):
        """Accept role (a)'s suggestions. They are translated into role (b)'s ops and
        applied by the same `apply_ops`, so the user reviews one diff and nothing is
        applied by a second, untested path. `notes` says what was NOT translated."""
        proposal = req.get("proposal") or {}
        suggestions = req.get("suggestions") or {}
        if not proposal.get("placements"):
            return {"error": "Brak struktury do poprawienia."}
        if not suggestions:
            return {"error": "Brak sugestii do zastosowania — najpierw poproś agenta (a)."}
        new_proposal, log, notes = AG.apply_suggestions(proposal, suggestions)
        new_proposal["tags"] = B.compute_tags(new_proposal)
        applied = [e for e in log if e["ok"]]
        return {"proposal": new_proposal, "log": log, "applied": len(applied),
                "skipped": len(log) - len(applied), "notes": notes}

    def _assist(self, req):
        """Agent (a): help build the structure at the low-confidence points.

        Returns SUGGESTIONS only — nothing is applied to the tree here. The user accepts
        them in the UI, and accepted mappings are what later gets promoted into config so
        the same case never needs the model again."""
        proposal = req.get("proposal") or {}
        ai_req = (proposal.get("ai") or {}).get("request")
        if not ai_req:
            return {"error": "Propozycja nie zawiera kontraktu dla AI — zbuduj ją ponownie."}
        if not AG.configured("N8N_STRUCTURE_URL"):
            return {"error": "Agent (a) nie jest podpięty — ustaw N8N_STRUCTURE_URL "
                             "na adres webhooka n8n i zrestartuj serve.py."}
        try:
            result = ai_fallback.interpret(ai_req, call=AG.structure_call())
        except AG.AgentError as e:
            return {"error": str(e), "aiRequest": ai_req}
        return {"suggestions": result,
                "escalations": (proposal.get("ai") or {}).get("escalations") or []}


if __name__ == "__main__":
    # `py scripts/serve.py [port] [--open]`  — kolejność argumentów dowolna
    args = sys.argv[1:]
    open_browser = "--open" in args
    ports = [a for a in args if a.isdigit()]
    port = int(ports[0]) if ports else 8765

    # Przeglądarkę otwiera SERWER, a nie skrypt startowy: tylko tutaj wiadomo, że
    # gniazdo jest już otwarte. Opóźnienie w .bat byłoby wyścigiem — przy zimnym
    # starcie import bibliotek Google trwa dłużej niż zwykle i użytkownik dostawał
    # „nie można połączyć się z serwerem”, mimo że sekundę później wszystko działa.
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"CM Worker UI + API na {url}  (Ctrl+C aby zatrzymać)")
    if open_browser:
        import webbrowser
        # osobny wątek: przy niektórych domyślnych przeglądarkach `open()` blokuje
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        print("Otwieram przeglądarkę… (zamknięcie TEGO okna zatrzymuje serwer)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymane.")
