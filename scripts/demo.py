"""End-to-end scripted demo of the whole pipeline (no npm needed).

  link + zip + source [+ message]  ->  advertiser/campaign match  ->  zip parse
  ->  line resolution/conflict  ->  proposal (+questions)  ->  [write]  ->  [tags]

Usage:
  py scripts/demo.py --link "<url>" --zip "<file.zip>" --source GDN [--message "..."]
                     [--execute] [--export]
Defaults to DRY-RUN (no writes). --execute performs real writes on the TEST
advertiser (guarded); --export writes the delta .xls of the created line.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
import parse_zip
import matcher as M
import build_proposal as B
from cm_auth import service
from cm_read import fetch_state, existing_tree
from match_link import _fetch_campaign_lps, TEST_PROFILE, TEST_ADVERTISER, MAP_PATH
from orchestrate import Orchestrator, resolve_tag_pairs
import export_tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", required=True)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--message", default="")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--json", metavar="PATH",
                    help="write the proposal JSON to PATH (load it in the UI) and stop")
    args = ap.parse_args()

    rules = json.load(open(MAP_PATH, encoding="utf-8"))["rules"]
    rule = M.resolve_advertiser(args.link, rules)
    if not rule:
        print("No advertiser rule matched -> AI fallback would resolve advertiser. Stop.")
        return
    anchor = rule.get("anchor", [])
    print(f"STEP 1  advertiser: {rule['advertiser']} (anchor={anchor or 'host'})  [TEST routing]")

    svc = service(read_only=not args.execute)
    camp_lps = _fetch_campaign_lps(svc, TEST_PROFILE, TEST_ADVERTISER)

    ranked, suggest_new = M.match_campaigns(args.link, anchor, camp_lps)
    if suggest_new:
        print("STEP 2  campaign: no path match -> SUGGEST NEW CAMPAIGN (not auto-created here)")
        return
    top = ranked[0]
    cid = top["campaignId"]
    print(f"STEP 2  campaign: {top['campaignName']} (id={cid}, common={top['common']})")

    this_lps = [l for l in camp_lps if l["campaignId"] == cid]
    line = M.resolve_line(args.link, anchor, args.source, this_lps)
    conflict = M.detect_line_conflict(args.link, anchor, args.source, this_lps)
    print(f"STEP 3  line: {line['lpName']} (creative linia{line['lineNumber']}, "
          f"reused={line['reused']})  conflict={conflict['conflict']}")

    parsed = parse_zip.parse(args.zip)
    print(f"STEP 4  zip: source~{parsed['source_hint']} format~{parsed['format_hint']} "
          f"units={parsed['n_units']} groups={[g['name'] for g in parsed.get('groups', [])]}")

    campaign = svc.campaigns().get(profileId=TEST_PROFILE, id=cid).execute()
    state = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
    proposal = B.build_proposal(
        args.source, parsed,
        {"id": cid, "name": campaign["name"], "status": "existing"},
        line, existing=existing_tree(state),
        campaign_lps=this_lps, target_url=args.link, line_conflict=conflict)
    print(f"STEP 5  proposal: {len(proposal['placements'])} placement(s), "
          f"{len(proposal['tags'])} tag(s), {len(proposal['questions'])} question(s)")
    if proposal["questions"]:
        for q in proposal["questions"]:
            print(f"          ? {q['id']}: {q['prompt'][:70]}")
    if args.message:
        print(f"          (message provided -> AI fallback would refine groups/lines/audience)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)
        print(f"\nwrote proposal -> {args.json}  (load it in the UI via 'Wczytaj JSON')")
        return

    print("\n=== PROPOSED STRUCTURE ===")
    orch = Orchestrator(svc, TEST_PROFILE, TEST_ADVERTISER, campaign, dry_run=not args.execute)
    log = orch.run(proposal, state)

    if args.execute and args.export:
        proposal["tags"] = B.compute_tags(proposal)
        fresh = fetch_state(svc, TEST_PROFILE, TEST_ADVERTISER, cid)
        pairs, missing = resolve_tag_pairs(fresh, proposal)
        if missing:
            print(f"WARN: nie rozwiązano {len(missing)} tagów: {missing[:3]}")
        if pairs:
            out = os.path.join(os.path.dirname(__file__), "..", "data", f"Tags_delta_{cid}.xls")
            campaign, adv, rows = export_tags.collect_rows(svc, TEST_PROFILE, cid, pairs)
            export_tags.write_xls(campaign, adv, rows, out)
            print(f"\nSTEP 6  tags: wrote {len(rows)} row(s) -> {out}")
    print("\n>> " + ("REAL WRITE done." if args.execute else "DRY-RUN only; nothing written."))


if __name__ == "__main__":
    main()
