"""AI fallback layer — invoked only when the deterministic rules aren't enough.

The pipeline is deterministic-first; this module (a) detects the ESCALATION points
where confidence is low, (b) builds a structured request for an AI Agent, and
(c) exposes a single `interpret()` seam. In production the Agent lives in n8n; here
`interpret` takes an optional callable so it can be wired to any LLM, and falls back
to an echo/mock when none is provided.
"""
import re

AUDIENCE_TERMS = ["prospecting", "retargeting", "remarketing", "lookalike", "lal",
                  "rmg", "strona_docelowa", "docelowa", "przelotka", "konwersje", "cta"]
URL_RE = re.compile(r"https?://\S+")


def escalations(parsed, proposal, message="", advertiser_rule=True):
    """Return the low-confidence decisions an AI Agent should help with."""
    out = []
    if not advertiser_rule:
        out.append({"code": "advertiser", "reason": "no URL rule matched the advertiser"})

    groups = parsed.get("groups") or []
    unknown = [g["name"] for g in groups if not g["source_hint"]]
    if unknown:
        out.append({"code": "group_mapping",
                    "reason": f"folders with unknown source/placement: {unknown}"})

    fmt = parsed.get("format_hint")
    if fmt in ("Karuzela", "Video") or any(u.get("card_index") for u in parsed.get("units", [])):
        out.append({"code": "ad_naming",
                    "reason": f"complex format '{fmt}' — ad naming (variant/dim/card) may need judgment"})

    urls = URL_RE.findall(message or "")
    terms = [t for t in AUDIENCE_TERMS if t in (message or "").lower()]
    if len(urls) > 1 or terms:
        out.append({"code": "lines_audience",
                    "reason": f"message implies multiple lines/audiences (urls={len(urls)}, terms={terms})"})

    if any("dimensionless" in w for w in parsed.get("warnings", [])):
        out.append({"code": "structure", "reason": "some zip entries lack a detectable dimension"})

    return out


def build_request(parsed, proposal, message, advertiser_list=None):
    """Structured payload handed to the AI Agent (n8n). Kept small + explicit."""
    return {
        "task_message": message,
        "url": proposal.get("line", {}).get("url"),
        "advertiser": proposal.get("site", {}).get("name"),
        "advertiser_list": advertiser_list or [],
        "zip": {
            "source_hint": parsed.get("source_hint"),
            "format_hint": parsed.get("format_hint"),
            "groups": parsed.get("groups"),
            "dimensions": parsed.get("dimensions"),
            "units": [{k: u.get(k) for k in ("dimension", "variant", "card_index", "type", "group")}
                      for u in parsed.get("units", [])],
        },
        "current_proposal": {
            "placements": [{"name": pl["name"], "group": pl.get("group"),
                            "ads": [a["name"] for a in pl["ads"]]} for pl in proposal["placements"]],
            "line": proposal.get("line"),
            "questions": proposal.get("questions"),
        },
        "instructions": (
            "You map creative zips to a CM360 tracking structure. Return JSON with: "
            "advertiser_guess, group_mappings [{folder, source, site, placement}], "
            "ad_naming [{unit, adName}], lines [{lpUrl, source, audience, lpName, creativeName}], "
            "resolved_questions {id: answer}, confidence 0..1, notes. "
            "Only fill fields you are confident about; leave others null for the human to decide."
        ),
    }


# Output schema the Agent must satisfy (for n8n structured-output / validation).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "advertiser_guess": {"type": ["string", "null"]},
        "group_mappings": {"type": "array"},
        "ad_naming": {"type": "array"},
        "lines": {"type": "array"},
        "resolved_questions": {"type": "object"},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
}


def interpret(request, call=None):
    """Run the AI Agent. `call` is any callable(prompt_dict)->dict (e.g. an n8n
    webhook or an LLM SDK). Without it, returns a mock so the flow stays runnable."""
    if call is not None:
        return call(request)
    return {
        "_mock": True,
        "advertiser_guess": request.get("advertiser"),
        "group_mappings": [], "ad_naming": [], "lines": [],
        "resolved_questions": {}, "confidence": 0.0,
        "notes": "AI Agent not wired (mock). In n8n this is the AI Agent node.",
    }


if __name__ == "__main__":
    import json, os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
    import parse_zip, build_proposal as B, matcher as M
    parsed = parse_zip.parse(os.path.join(os.path.dirname(__file__), "..", "data",
                                          "samples", "Materiały standardowe_.zip"))
    line = {"lineNumber": 6, "lpName": "linia6-GDN", "source": "GDN", "path": "x", "reused": False}
    prop = B.build_proposal("GDN", parsed, {"id": "C", "name": "demo", "status": "existing"}, line,
                            target_url="https://x")
    msg = ("te same grafiki otagować pod stronę docelową\n"
           "LP (prospecting): https://.../?utm_medium=prospecting\n"
           "LP (remarketing): https://.../?utm_medium=remarketing")
    esc = escalations(parsed, prop, msg)
    print("ESCALATIONS:", json.dumps(esc, ensure_ascii=False, indent=2))
    print("AI REQUEST (truncated):", json.dumps(build_request(parsed, prop, msg), ensure_ascii=False)[:400])
    print("AI RESULT (mock):", json.dumps(interpret(build_request(parsed, prop, msg)), ensure_ascii=False))
