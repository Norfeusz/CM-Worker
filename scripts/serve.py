"""Local backend (stdlib only, no npm) — serves the UI AND builds proposals.

  GET  /                     -> ui/index.html
  GET  /<file>               -> static file from ui/
  POST /api/build-proposal   -> {link, source, message, zipB64|zipPath} -> proposal JSON

Run:  py scripts/serve.py   (then open http://127.0.0.1:8765/)
Read-only against the TEST advertiser (cm_auth guard still applies).
"""
import base64
import json
import os
import sys
import tempfile
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
from match_link import _fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER, MAP_PATH

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
         ".css": "text/css", ".json": "application/json"}


def build_proposal(link, zip_path, source, message="", campaign_id=None, new_campaign=None):
    rules = json.load(open(MAP_PATH, encoding="utf-8"))["rules"]
    rule = M.resolve_advertiser(link, rules)
    if not rule:
        return {"error": "Żadna reguła nie dopasowała advertisera (tu wejdzie fallback AI)."}
    anchor = rule.get("anchor", [])
    svc = service(read_only=True)

    if new_campaign:
        # brand-new campaign: no campaign LPs yet, so this link is always line 1 and
        # every placement/ad/creative below is new. Account-level sites still exist,
        # so pass them as an empty-placement tree to keep the Site badge honest.
        state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER)
        parsed = parse_zip.parse(zip_path)
        prop = B.build_proposal(source, parsed,
                                {"id": None, "name": new_campaign, "status": "new"},
                                M.resolve_line(link, anchor, source, []),
                                existing={s: {} for s in state["sites_by_name"]},
                                campaign_lps=[], target_url=link)
        return _attach_ai(prop, parsed, message, rules)

    camp_lps = _fetch_campaign_lps(svc, TEST_PROFILE, TEST_ADVERTISER)
    if campaign_id:
        # explicit override (user manually picked a campaign from the browse list)
        cid = campaign_id
    else:
        ranked, suggest_new = M.match_campaigns(link, anchor, camp_lps)
        if suggest_new:
            return {"suggestNewCampaign": True, "advertiser": rule.get("advertiser"),
                    "message": "Brak kampanii z pasującą ścieżką — zasugerowano utworzenie nowej.",
                    "pathHint": "/".join(M.remaining_path(link, anchor) or []),
                    "candidates": ranked[:5]}
        cid = ranked[0]["campaignId"]

    this = [l for l in camp_lps if l["campaignId"] == cid]
    line = M.resolve_line(link, anchor, source, this)
    conflict = M.detect_line_conflict(link, anchor, source, this)
    parsed = parse_zip.parse(zip_path)
    campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
    state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
    prop = B.build_proposal(source, parsed,
                            {"id": cid, "name": campaign["name"], "status": "existing"},
                            line, existing=existing_tree(state), campaign_lps=this,
                            target_url=link, line_conflict=conflict)
    return _attach_ai(prop, parsed, message, rules)


def _attach_ai(proposal, parsed, message, rules):
    """Attach the escalation points and the ready-made agent (a) request.

    Carrying the request in the proposal means /api/assist needs no re-upload and no
    re-parse of the zip — the client just hands back what it already has. Escalations
    are informational: an empty list means the deterministic rules were enough and no
    model is needed for this order.
    """
    advertisers = sorted({r.get("advertiser") for r in rules if r.get("advertiser")})
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
            if not cid or not site:
                return self._send(400, json.dumps({"error": "wymagane: campaignId, site"}))
            try:
                svc = service(read_only=True)
                state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
                return self._send(200, json.dumps(site_structure(state, site), ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
        if route == "/api/campaigns":
            try:
                svc = service(read_only=True)
                camps = _paginate(svc.campaigns, "campaigns", profileId=TEST_PROFILE,
                                  advertiserIds=[TEST_ADVERTISER], sortField="NAME")
                out = [{"id": c["id"], "name": c["name"]} for c in camps]
                return self._send(200, json.dumps({"campaigns": out}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))
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
            if route == "/api/commit":
                return self._send(200, json.dumps(self._commit(req), ensure_ascii=False))
            if route == "/api/create-site":
                return self._send(200, json.dumps(self._create_site(req), ensure_ascii=False))
            return self._send(404, json.dumps({"error": "unknown endpoint"}))
        except Exception as e:
            self._send(500, json.dumps({"error": friendly_error(e)}, ensure_ascii=False))

    def _build(self, req):
        zip_path = req.get("zipPath")
        if req.get("zipB64"):
            tmp = os.path.join(tempfile.gettempdir(), req.get("zipName", "up.zip"))
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(req["zipB64"].split(",")[-1]))
            zip_path = tmp
        if not req.get("link") or not zip_path or not req.get("source"):
            return {"error": "wymagane: link, zip, source"}
        return build_proposal(req["link"], zip_path, req["source"], req.get("message", ""),
                              campaign_id=req.get("campaignId"),
                              new_campaign=req.get("newCampaign"))

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
        svc = service(read_only=dry)
        if is_new:
            # nothing to read yet — the orchestrator creates it and assigns dates/id
            campaign, state = camp_spec, fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER)
        else:
            campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
            state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
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
        orch = Orchestrator(svc, TEST_PROFILE, TEST_ADVERTISER, campaign, dry_run=dry)
        log = orch.run(proposal, state)
        out = {"dryRun": dry, "log": log, "campaignId": orch.cid}
        if not dry:
            cid = orch.cid                 # a brand-new campaign only has an id now
            # recompute tags fresh from the (possibly user-edited) placements — the
            # client may have added placements/ads/creatives after the proposal was
            # first built, so proposal["tags"] as received can be stale.
            proposal["tags"] = B.compute_tags(proposal)
            # refetch fresh state (everything just written now exists) and resolve
            # the proposal's (site/placement/ad/creative) tag rows to live ids
            fresh = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
            pairs, missing = resolve_tag_pairs(fresh, proposal)
            if pairs:
                path = os.path.join(os.path.dirname(__file__), "..", "data", f"Tags_delta_{cid}.xls")
                camp, adv, rows = export_tags.collect_rows(svc, TEST_PROFILE, cid, pairs)
                export_tags.write_xls(camp, adv, rows, path)
                out["tags"] = {"file": os.path.abspath(path), "count": len(rows),
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"CM Worker UI + API na http://127.0.0.1:{port}/  (Ctrl+C aby zatrzymać)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
