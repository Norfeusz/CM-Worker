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
        self.cid = campaign["id"]
        self.dry = dry_run
        self.log = []
        sd = campaign["startDate"]
        ed = campaign["endDate"]
        self.start_date, self.end_date = sd, ed
        # ad startTime must not be in the past -> use now+5min (still within campaign)
        now = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
        self.start_time = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.end_time = f"{ed}T23:59:00.000Z"

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

    def run(self, proposal, state):
        print(f"CAMPAIGN {self.cid} '{self.campaign['name']}'  "
              f"({'DRY-RUN' if self.dry else 'REAL WRITE'})  dates {self.start_date}..{self.end_date}\n")
        site = proposal["site"]["name"]
        line_lp_name = proposal["line"]["lpName"]

        # 1) landing pages: resolve/create every DISTINCT LP referenced anywhere in the
        # proposal — normally just the shared line LP, but a creative may override its
        # own (e.g. linia4-słońce / linia4-niebo pointing at two different URLs).
        lp_wanted = dict([(proposal["line"]["lpName"], proposal["line"]["url"] or "")] + [
            self._lp_key(proposal, cr) for pl in proposal["placements"]
            for a in pl["ads"] for cr in a["creatives"]])
        lp_ids = {}
        for name, url in lp_wanted.items():
            lp_in_campaign = name in state["lps_by_name"]
            lp_id = state["lps_by_name"].get(name) or state["adv_lp_by_name_url"].get((name, url))
            if lp_id:
                self._rec("REUSE", "landingPage", name, lp_id)
            else:
                r = W.landing_page(self.svc, self.pid, self.adv, name, url, dry_run=self.dry)
                lp_id = r.get("id", "(new)")
                self._rec("CREATE", "landingPage", name, r.get("id"), url)
            # register in the campaign's landing-page list (unless already there); only
            # the shared LINE LP is eligible to become the campaign default
            if not lp_in_campaign:
                first_line = not self.campaign.get("defaultLandingPageId") and name == line_lp_name
                W.add_lp_to_campaign(self.svc, self.pid, self.cid, lp_id,
                                     make_default=first_line, dry_run=self.dry)
                self._rec("REGISTER", "campaign-LP", name, lp_id,
                          "as default (first line)" if first_line else "added to campaign list")
            lp_ids[name] = lp_id

        # 2) site (reuse; creation is an edge case that needs directory-site linkage)
        site_id = state["sites_by_name"].get(site)
        if site_id:
            self._rec("REUSE", "site", site, site_id)
        else:
            r = W.create_site(self.svc, self.pid, site, dry_run=self.dry)
            site_id = r.get("id", "(new)")
            self._rec("CREATE", "site", site, r.get("id"),
                      "Site Directory: znajdź istniejący albo dodaj nowy + podepnij do konta")

        # 3) creatives: ensure EVERY distinct creative name used anywhere in the
        # proposal exists + is campaign-associated (an ad can carry several, e.g.
        # linia4-słońce + linia4-niebo on the same dimension).
        all_names = sorted({cr["name"] for pl in proposal["placements"]
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
            p_id = state["placements"].get((site, pl["name"]))
            if p_id:
                self._rec("REUSE", "placement", pl["name"], p_id)
            else:
                r = W.placement(self.svc, self.pid, self.cid, site_id, pl["name"],
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
