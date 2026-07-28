"""Dry-run write planner: proposal + live CM360 state -> ordered operation plan.

Decides REUSE(existing id) vs CREATE for every object (landing page, site,
placement, ad, creative, assignment) and lists them in dependency order. Prints
the plan only. NO writes happen here and the read-only guard stays on.

Usage:
  py scripts/plan_writes.py <proposal.json> <profileId> <advertiserId> [campaignId]
  py scripts/plan_writes.py --demo        # build a proposal live for the test campaign
"""
import json
import os
import sys

import matcher as M

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import build_proposal as B
from cm_auth import service
from cm_read import fetch_state
from match_link import _fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER

Op = lambda action, kind, name, detail="", cid=None: {
    "action": action, "kind": kind, "name": name, "detail": detail, "id": cid}


def plan(proposal, state):
    ops = []
    site = proposal["site"]["name"]
    site_id = state["sites_by_name"].get(site)
    ops.append(Op("REUSE" if site_id else "CREATE", "site", site,
                  "account-level", site_id))

    # line / landing page
    lp_name = proposal["line"]["lpName"]
    lp_id = state["lps_by_name"].get(lp_name)
    ops.append(Op("REUSE" if lp_id else "CREATE", "landingPage", lp_name,
                  proposal["line"].get("url") or "", lp_id))

    cre_name = proposal["line"]["creativeName"]
    for pl in proposal["placements"]:
        p_id = state["placements"].get((site, pl["name"]))
        ops.append(Op("REUSE" if p_id else "CREATE", "placement", pl["name"],
                      f"site={site} compat={pl['compatibility']} size={pl['size']}", p_id))
        for a in pl["ads"]:
            ad_key = (site, pl["name"], a["name"])
            ad_id = state["ads"].get(ad_key)
            ops.append(Op("REUSE" if ad_id else "CREATE", "ad", a["name"],
                          f"placement={pl['name']}", ad_id))
            # creative
            cid = state["creatives_by_name"].get(cre_name)
            ops.append(Op("REUSE" if cid else "CREATE", "creative", cre_name,
                          f"type={a['creative']['type']} asset={a['creative'].get('source_path')}", cid))
            # assignment (ad -> creative)
            assigned = cre_name in state["ad_creatives"].get(ad_key, set())
            ops.append(Op("NO-OP" if assigned else "ASSIGN", "assignment",
                          f"{a['name']} -> {cre_name}",
                          "already linked" if assigned else f"clickThrough={lp_name}"))

    n_tags = len(proposal["tags"])
    ops.append(Op("GENERATE", "tags", f"{n_tags} tag(s)",
                  "via placements.generatetags after creation"))
    return ops


def print_plan(proposal, ops):
    c = proposal["campaign"]
    print(f"CAMPAIGN: [{c['status']}] {c['name']} (id={c.get('id') or '— NEW —'})")
    print(f"  (new campaign also needs: default landing page + start/end dates)\n"
          if c["status"] == "new" else "")
    counts = {}
    for o in ops:
        counts[o["action"]] = counts.get(o["action"], 0) + 1
        idpart = f" id={o['id']}" if o["id"] else ""
        print(f"  {o['action']:8} {o['kind']:12} {o['name']:30}{idpart}"
              f"   {o['detail']}")
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(">> DRY-RUN: nothing was written. Read-only guard still ON.")


def demo():
    """Build a proposal live for test campaign 36430023 and plan it."""
    CID = "36430023"
    anchor = ["indywidualny", "ubezpieczenia"]
    svc = service(read_only=True)
    all_lps = _fetch_campaign_lps(svc, TEST_PROFILE, TEST_ADVERTISER)
    camp_lps = [l for l in all_lps if l["campaignId"] == CID]
    state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, CID)

    url = ("https://www.mbank.pl/lp2/2026/c1/indywidualny/ubezpieczenia/"
           "nieruchomosci/promocja/?utm_source=google&utm_medium=gdn")
    line = M.resolve_line(url, anchor, "GDN", camp_lps)
    parsed = parse_zip.parse(os.path.join(
        os.path.dirname(__file__), "..", "data", "samples", "GDN Citi.zip"))

    # existing tree from live state -> {site:{placement:{ad:[creatives]}}}
    existing = {}
    for (sn, pn), _pid in state["placements"].items():
        existing.setdefault(sn, {}).setdefault(pn, {})
    for (sn, pn, an), cres in state["ad_creatives"].items():
        existing.setdefault(sn, {}).setdefault(pn, {})[an] = list(cres)

    camp = {"id": CID, "name": "Household 06-08.2026 - testy", "status": "existing"}
    proposal = B.build_proposal("GDN", parsed, camp, line, existing=existing,
                                campaign_lps=camp_lps, target_url=url)
    print_plan(proposal, plan(proposal, state))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo(); sys.exit(0)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 3:
        print(__doc__); sys.exit(1)
    proposal = json.load(open(args[0], encoding="utf-8"))
    svc = service(read_only=True)
    state = fetch_state(svc, args[1], args[2], args[3] if len(args) > 3 else None)
    print_plan(proposal, plan(proposal, state))
