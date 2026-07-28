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
from cm_auth import service
from cm_read import fetch_state, search_sites, existing_tree, site_structure, _paginate
from match_link import _fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER, MAP_PATH

UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui")
CTYPE = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".css": "text/css", ".json": "application/json"}


def build_proposal(link, zip_path, source, message="", campaign_id=None):
    rules = json.load(open(MAP_PATH, encoding="utf-8"))["rules"]
    rule = M.resolve_advertiser(link, rules)
    if not rule:
        return {"error": "Żadna reguła nie dopasowała advertisera (tu wejdzie fallback AI)."}
    anchor = rule.get("anchor", [])
    svc = service(read_only=True)
    camp_lps = _fetch_campaign_lps(svc, TEST_PROFILE, TEST_ADVERTISER)

    if campaign_id:
        # explicit override (user manually picked a campaign from the browse list)
        cid = campaign_id
    else:
        ranked, suggest_new = M.match_campaigns(link, anchor, camp_lps)
        if suggest_new:
            return {"suggestNewCampaign": True, "advertiser": rule.get("advertiser"),
                    "message": "Brak kampanii z pasującą ścieżką — zasugerowano utworzenie nowej.",
                    "candidates": ranked[:5]}
        cid = ranked[0]["campaignId"]

    this = [l for l in camp_lps if l["campaignId"] == cid]
    line = M.resolve_line(link, anchor, source, this)
    conflict = M.detect_line_conflict(link, anchor, source, this)
    parsed = parse_zip.parse(zip_path)
    campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
    state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
    return B.build_proposal(source, parsed,
                            {"id": cid, "name": campaign["name"], "status": "existing"},
                            line, existing=existing_tree(state), campaign_lps=this,
                            target_url=link, line_conflict=conflict)


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
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
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
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
        if route == "/api/campaigns":
            try:
                svc = service(read_only=True)
                camps = _paginate(svc.campaigns, "campaigns", profileId=TEST_PROFILE,
                                  advertiserIds=[TEST_ADVERTISER], sortField="NAME")
                out = [{"id": c["id"], "name": c["name"]} for c in camps]
                return self._send(200, json.dumps({"campaigns": out}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
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
                return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
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
            if route == "/api/commit":
                return self._send(200, json.dumps(self._commit(req), ensure_ascii=False))
            if route == "/api/create-site":
                import cm_write as W
                r = W.create_site(service(read_only=True), TEST_PROFILE,
                                  req.get("name"), req.get("url"), dry_run=True)
                return self._send(200, json.dumps(
                    {"planned": True, "name": req.get("name"),
                     "note": "Plan (dry-run): znajdź w Site Directory albo dodaj + podepnij do konta. "
                             "Realne dodanie wymaga potwierdzenia (zapis)."}, ensure_ascii=False))
            return self._send(404, json.dumps({"error": "unknown endpoint"}))
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))

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
                              campaign_id=req.get("campaignId"))

    def _commit(self, req):
        """Run the orchestrator on the (effective) proposal. dryRun=True -> read-only
        plan; dryRun=False -> real writes (scoped guard) + tag .xls delta."""
        from orchestrate import Orchestrator, resolve_tag_pairs
        import export_tags
        proposal = req.get("proposal") or {}
        dry = req.get("dryRun", True)
        cid = (proposal.get("campaign") or {}).get("id")
        if not cid:
            return {"error": "Brak campaign.id — najpierw zbuduj propozycję."}
        svc = service(read_only=dry)
        campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
        state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
        orch = Orchestrator(svc, TEST_PROFILE, TEST_ADVERTISER, campaign, dry_run=dry)
        log = orch.run(proposal, state)
        out = {"dryRun": dry, "log": log}
        if not dry:
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
        """Pass user remarks + current structure to the AI seam. Live LLM lives in
        n8n (ai_fallback.interpret with a real `call`); here it returns the mock."""
        proposal, remarks = req.get("proposal", {}), req.get("remarks", "")
        ai_req = {"remarks": remarks, "current_proposal": proposal,
                  "answers": req.get("answers"),
                  "instructions": "Apply the human remarks and return a corrected proposal (same schema)."}
        result = ai_fallback.interpret(ai_req)  # call=None -> mock until wired in n8n
        if result.get("_mock"):
            return {"notes": (f"Agent AI (n8n) nie jest jeszcze podpięty. Uwagi przekazane: "
                              f"„{remarks}”. Po podpięciu Agent zwróci poprawioną strukturę."),
                    "aiRequest": ai_req}
        return result


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"CM Worker UI + API na http://127.0.0.1:{port}/  (Ctrl+C aby zatrzymać)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
