"""Two AI agent roles, called through n8n. Stdlib only (serve.py has no deps).

Role (a) STRUCTURE  — helps build the initial proposal when deterministic rules run out
                      (unknown source folders, complex ad naming, lines from the message).
                      Its approved answers get promoted into config by `promote.py`, so an
                      analogous case never needs the AI again.
Role (b) INTENT     — reads the user's freeform remarks about a proposal it doesn't like and
                      returns EDIT OPERATIONS, not a rewritten tree (see below).

Direction of traffic: this module calls OUT to an n8n webhook. n8n hosts the model and
returns validated JSON. Nothing about CM360 writes goes through n8n — the cm_auth guard
stays in Python.

Config (environment, never committed):
  N8N_STRUCTURE_URL   webhook for role (a)
  N8N_INTENT_URL      webhook for role (b)
  N8N_TOKEN           optional; sent as X-CM-Token if set
  N8N_TIMEOUT         seconds, default 120

Why (b) returns operations instead of a corrected proposal: a model handing back a whole
tree can silently drop or mangle nodes, and there is nothing to review. A short op list is
schema-checkable, applied deterministically in `apply_ops`, shown to the user as a diff,
and each op maps 1:1 to something the UI can already do by hand.
"""
import json
import os
import urllib.error
import urllib.request

MODEL_NOTE = "model wybierany w n8n (węzeł Chat Model) — nie tutaj"
DEFAULT_TIMEOUT = int(os.environ.get("N8N_TIMEOUT", "120"))


# ---- output contracts -------------------------------------------------------
# Kept strict-mode friendly on purpose: every object closed with
# additionalProperties=false, every key required, no numeric/length constraints,
# no free-form maps (dicts become arrays of {key, value}). That is the subset
# structured-output modes can actually enforce.

STRUCTURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["advertiser_guess", "group_mappings", "ad_naming", "lines",
                 "resolved_questions", "confidence", "notes"],
    "properties": {
        "advertiser_guess": {"type": ["string", "null"]},
        "group_mappings": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["folder", "source", "site", "placement", "adKey",
                             "confidence", "reason"],
                "properties": {
                    "folder": {"type": "string"},
                    "source": {"type": "string"},
                    "site": {"type": "string"},
                    "placement": {"type": "string"},
                    "adKey": {"type": "string",
                              "enum": ["dimension", "variant", "variant_dim_card"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
        "ad_naming": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["unit", "adName", "reason"],
                "properties": {"unit": {"type": "string"}, "adName": {"type": "string"},
                               "reason": {"type": "string"}},
            },
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["lpUrl", "source", "audience", "lpName", "creativeName"],
                "properties": {"lpUrl": {"type": "string"}, "source": {"type": "string"},
                               "audience": {"type": ["string", "null"]},
                               "lpName": {"type": "string"},
                               "creativeName": {"type": "string"}},
            },
        },
        "resolved_questions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "answer"],
                "properties": {"id": {"type": "string"},
                               "answer": {"type": ["string", "array"]}},
            },
        },
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
}

# Every op below maps to something a trafficker can already do by hand in the UI.
OPS = ["rename_placement", "rename_ad", "rename_creative", "rename_creative_all",
       "move_ad", "add_placement",
       "add_ad", "add_creative", "delete_ad", "delete_creative", "set_creative_lp",
       "apply_creative_to_all"]

INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ops", "confidence", "notes", "unclear"],
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["op", "placement", "ad", "creative", "name", "to",
                             "lpName", "lpUrl", "reason"],
                "properties": {
                    "op": {"type": "string", "enum": OPS},
                    # Addressing fields; unused ones must be sent as null, not omitted —
                    # strict schemas cannot express "one of these shapes".
                    "placement": {"type": ["string", "null"]},
                    "ad": {"type": ["string", "null"]},
                    "creative": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "to": {"type": ["string", "null"]},
                    "lpName": {"type": ["string", "null"]},
                    "lpUrl": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
        # Anything the agent could NOT turn into an op — surfaced to the user as a
        # question instead of being guessed at.
        "unclear": {"type": "array", "items": {"type": "string"}},
    },
}


# ---- system prompts --------------------------------------------------------
# The CM360 domain model these encode is the validated one from CLAUDE.md; keep the two
# in sync. Both prompts end with the same rule: leave a field null rather than guess.

_DOMAIN = """You map advertising creative deliveries onto a Campaign Manager 360 (CM360)
tracking structure for a Polish media agency. CM360 here is used for TRACKING ONLY: every
creative is a 1x1 TRACKING_TEXT template, no real assets are uploaded.

Object model (validated against live data — treat as ground truth):
  advertiser  <- URL path segments (ignoring a /lp2/2026/c1/-style prefix)
  campaign    <- landing pages live AT CAMPAIGN LEVEL, never shared between campaigns
  source      -> Site        (GDN->CG_GDN, Facebook/Meta->CG_Facebook, DemGen->CG_Demand_Gen,
                              Programmatic->CG_Programmatic, Mailing->mailsales.pl)
  format      -> Placement   (always compatibility=DISPLAY, size=1x1; GDN->"Display";
                              Facebook->Link/Animacje/Karuzela/Posty; DemGen->Display+Karuzela)
  dimension/variant -> Ad    (GDN: the dimension e.g. "300x250"; DemGen: the variant e.g.
                              "demgen1" and the dimension is IGNORED; Facebook carousel:
                              "{variant}_{dimension}_{card}")
  line (LP + audience) -> Creative  (e.g. "linia3", "linia4-slonce", "refinans-prospecting")

Hard rules:
  * ONE TAG = ONE TRIPLE (Placement x Ad x Creative). A single Ad may carry MANY creatives.
  * Ad and Placement names come from the zip structure + the source convention.
  * Lines and audiences come from the ORDER MESSAGE, never from the zip.
  * A line number is tied to the destination PATH within a campaign: same path => same line.
  * NEVER invent a line number. If the tool already resolved one (it is in the proposal),
    build on it; a new number is only for a path that has none.

NAMING — these two are different and must not be mixed up:
  * Landing page name = "linia{N}-{SOURCE}[-{distinguishing word}]"
    ORDER IS MANDATORY: line number, then SOURCE (GDN/Facebook/DemGen/...), and only
    then the distinguishing word, and only when there is one.
        linia2-GDN                 one landing page for line 2
        linia1-Facebook-lookalike  line 1 has several pages; this is the lookalike one
    The segment right after the number is ALWAYS the source, never the audience.
    Do NOT put the distinguishing word in the middle: "linia1-lookalike-Facebook" is
    the OLD form and is no longer valid — with it the source cannot be read back out
    of the name, because nothing says which segment it is.
  * Creative name = "linia{N}[-{distinguishing word}]" — NO source at all.
        linia2, linia1-lookalike, linia4-slonce
    Creative names carry the AUDIENCE; landing page names carry the SOURCE.
"""

STRUCTURE_SYSTEM = _DOMAIN + """
You are role (a): you assist the deterministic parser at points where it is unsure. You are
given the parsed zip, the current proposal, and the order message.

Fill ONLY what you are confident about. Use null / an empty array for anything else — a
human trafficker reviews every field you return, and a wrong confident answer costs more
than an admitted gap.

`group_mappings` is ONLY for source/format SUBFOLDERS that actually exist in the zip and
that the rules could not map. A zip with creatives at the root has no groups — return an
empty array. Never emit an entry with an empty `folder`: approved mappings are written into
the tool's config as permanent rules, and a rule keyed on "" would be junk forever. Put your reasoning for each mapping in its `reason` field, short and
concrete (what in the folder name or message led you there), because an approved mapping is
written back into the tool's config as a permanent rule.

`confidence` is your overall confidence in the whole answer, 0..1.
"""

INTENT_SYSTEM = _DOMAIN + """
You are role (b): the trafficker was shown a proposed structure, is not happy with it, and
wrote freeform remarks in Polish. Translate their intent into a list of EDIT OPERATIONS
against the current structure.

FIELD USAGE PER OP — use exactly these; set every other field to null. Getting this wrong
means the operation is SKIPPED and the user's request silently does not happen, so treat it
as strictly as the schema itself:

  rename_placement       placement = current name,  to = new name
  add_placement          name      = new placement name
  rename_ad              placement, ad = current ad name,  to = new ad name
  add_ad                 placement, name = new ad name
  delete_ad              placement, ad
  move_ad                placement = SOURCE placement, ad, to = TARGET placement name
  add_creative           placement, ad, name = creative name, optional lpName + lpUrl
  rename_creative        placement, ad, creative = current name, to = new name
  rename_creative_all    creative = current name, to = new name, optional lpName + lpUrl
                         (no placement/ad — renames it on EVERY ad that has it)
  delete_creative        placement, ad, creative
  set_creative_lp        placement, ad, creative, lpName, lpUrl
  apply_creative_to_all  name = creative name, optional lpName + lpUrl   (no placement/ad)

Note the pattern: `to` always holds the NEW value (a new name, or the target placement for
move_ad). `name` holds the name of a node being CREATED. The node being acted upon is
addressed by placement / ad / creative.

You are given `zip` — the parsed delivery (dimensions, variants, per-unit type and folder).
Use it whenever the remarks refer to what is in the package ("wymiary zgodne z zawartością
paczki", "folder GIF to osobny placement"). A unit's `variant` is its top-level folder, so a
delivery split into GIF/HTML/PNG folders shows up as units with those variants — that is how
you know which dimensions belong in a placement named after a folder. Do not claim the zip
contents are unavailable; they are in `zip`.

Rules:
  * Only emit ops that the remarks actually justify. Do not tidy, reorder, or "improve"
    anything they did not mention.
  * A newly created placement is EMPTY. If the remarks imply it should hold ads, emit the
    add_ad ops too — creating an empty placement and then asking which ads go in it wastes
    a round trip when `zip` already answers the question.
  * Address nodes by their CURRENT names, exactly as given in the structure.
  * Set every field you do not need to null (the schema requires all keys present).
  * Anything you cannot confidently turn into an op goes into `unclear` as a short question
    in Polish — do not guess it into an op.
  * RENAMING A LINE EVERYWHERE is `rename_creative_all`, never `apply_creative_to_all`.
    "Dopisz coś do nazwy linii", "linie mają nazywać się linia8-GDN-firmootwieracz" = a rename.
    `apply_creative_to_all` ADDS the creative to every ad, so using it for a rename first
    duplicates every line, and deleting the old names then leaves every line sitting on
    every ad — destroying which materials each page actually received. This happened; do
    not repeat it.
  * `apply_creative_to_all` is only for a remark that genuinely asks to PUT a creative on
    every ad ("ten creative na wszystkich adach"). Different ads holding different
    creatives is not untidiness — it records which dimensions each landing page's folder
    delivered, and the tool refuses to add across a structure shaped that way.
  * To give one ad a creative it lacks, use `add_creative` on that placement + ad.
"""


# ---- minimal JSON Schema validation (stdlib; covers the subset used above) ---
def validate(obj, schema, path="$"):
    """Return a list of human-readable problems; empty list means valid."""
    errs = []
    types = schema.get("type")
    types = [types] if isinstance(types, str) else (types or [])
    if types and not _type_ok(obj, types):
        return [f"{path}: oczekiwano {'/'.join(types)}, jest {_kind(obj)}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} nie jest jedną z {schema['enum']}")
    if "object" in types and isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                errs.append(f"{path}.{key}: brak wymaganego pola")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    errs.append(f"{path}.{key}: nieoczekiwane pole")
        for key, sub in props.items():
            if key in obj:
                errs.extend(validate(obj[key], sub, f"{path}.{key}"))
    if "array" in types and isinstance(obj, list) and "items" in schema:
        for i, item in enumerate(obj):
            errs.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errs


def _kind(o):
    return {type(None): "null", bool: "boolean", int: "number", float: "number",
            str: "string", list: "array", dict: "object"}.get(type(o), type(o).__name__)


def _type_ok(o, types):
    for t in types:
        if t == "null" and o is None:
            return True
        if t == "boolean" and isinstance(o, bool):
            return True
        if t == "number" and isinstance(o, (int, float)) and not isinstance(o, bool):
            return True
        if t == "string" and isinstance(o, str):
            return True
        if t == "array" and isinstance(o, list):
            return True
        if t == "object" and isinstance(o, dict):
            return True
    return False


# ---- n8n transport ---------------------------------------------------------
class AgentError(RuntimeError):
    """Raised for anything that makes an agent answer unusable (transport or schema)."""


def n8n_call(url_env, schema, system, timeout=None):
    """Build a `call(request) -> dict` for ai_fallback.interpret, backed by an n8n webhook.

    The prompt and the schema travel WITH the request, so they stay versioned in this repo
    and n8n stays a thin relay that owns only the API key and the model choice. The webhook
    must answer with the agent's JSON object (n8n's "Respond to Webhook").

    A reply that does not satisfy `schema` is rejected here rather than passed on — a
    half-valid answer downstream is worse than a visible failure.
    """
    def call(request):
        url = os.environ.get(url_env)
        if not url:
            raise AgentError(
                f"Brak {url_env} w środowisku — ustaw adres webhooka n8n "
                f"(np. set {url_env}=https://n8n.firma/webhook/cm-worker-...).")
        payload = {"system": system, "schema": schema, "input": request}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("N8N_TOKEN")
        if token:
            headers["X-CM-Token"] = token
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise AgentError(f"n8n odpowiedziało {e.code}: {e.read()[:300]!r}") from e
        except Exception as e:
            secs = timeout or DEFAULT_TIMEOUT
            name = type(e).__name__
            # A timeout is NOT "unreachable": the connection succeeded and n8n simply
            # did not answer, which in practice means the model/provider node inside the
            # workflow hung. Saying "nieosiągalne" sends the user to check the network,
            # which is the wrong place to look.
            if name in ("TimeoutError", "timeout", "socket.timeout") or \
                    "timed out" in str(e).lower():
                raise AgentError(
                    f"n8n nie odpowiedziało w ciągu {secs}s. Połączenie działa, więc "
                    f"najczęściej zawiesił się węzeł modelu w workflow — zajrzyj w n8n "
                    f"w „Executions”, tam widać prawdziwy błąd (np. brak odpowiedzi "
                    f"dostawcy). Dłuższy limit: N8N_TIMEOUT w start.bat. [{name}]") from e
            raise AgentError(
                f"n8n nieosiągalne — sprawdź sieć/VPN i adres webhooka "
                f"({name}: {e})") from e
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise AgentError(f"n8n zwróciło nie-JSON: {raw[:300]!r}") from e
        # n8n commonly wraps a single item as [{...}] or {"output": {...}}
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if isinstance(data, dict) and "output" in data and isinstance(data["output"], dict):
            data = data["output"]
        problems = validate(data, schema)
        if problems:
            raise AgentError("Odpowiedź agenta nie pasuje do schematu: "
                             + "; ".join(problems[:5]))
        return data
    return call


def structure_call(timeout=None):
    return n8n_call("N8N_STRUCTURE_URL", STRUCTURE_SCHEMA, STRUCTURE_SYSTEM, timeout)


def intent_call(timeout=None):
    return n8n_call("N8N_INTENT_URL", INTENT_SCHEMA, INTENT_SYSTEM, timeout)


def configured(url_env):
    """Whether a role is wired up — lets the UI hide what cannot work yet."""
    return bool(os.environ.get(url_env))


# ---- request builder for role (b) ------------------------------------------
def build_intent_request(proposal, remarks, answers=None):
    """Compact view of the current structure + the zip contents + the user's remarks.

    The zip summary matters: remarks routinely reference it ("wymiary zgodnie z zawartością
    paczki", "foldery GIF/HTML to osobne placementy"). Without it the agent correctly
    refuses to guess and returns zero ops, which reads like a failure but is the honest
    answer to an incomplete request. It is reused from ai.request (built at proposal time),
    so there is no re-upload and no re-parse of the zip here.
    """
    zip_view = ((proposal.get("ai") or {}).get("request") or {}).get("zip")
    return {
        "remarks": remarks,
        "answers": answers,
        "zip": zip_view,
        "structure": {
            "campaign": (proposal.get("campaign") or {}).get("name"),
            "source": proposal.get("source"),
            "site": (proposal.get("site") or {}).get("name"),
            "line": {k: (proposal.get("line") or {}).get(k)
                     for k in ("number", "lpName", "creativeName", "url")},
            "placements": [{
                "name": pl["name"],
                "ads": [{"name": a["name"],
                         "creatives": [{"name": c["name"], "lpName": c.get("lpName")}
                                       for c in a["creatives"]]}
                        for a in pl["ads"]],
            } for pl in proposal.get("placements", [])],
        },
        "allowed_ops": OPS,
        "instructions": ("Return edit operations that realise the remarks. Leave unused "
                         "fields null. Put anything ambiguous in `unclear` instead of "
                         "guessing. `zip` is the parsed delivery — use it whenever the "
                         "remarks refer to what is in the package."),
    }


# ---- deterministic application of role (b)'s ops ----------------------------
# The agent proposes; this code decides. Every lookup is scoped by placement (and ad)
# rather than by bare name: matching a creative on name alone once renamed the wrong
# neighbouring creative, and that bug must not come back through the AI path.

def _find(seq, name):
    return next((x for x in seq if x["name"] == name), None)


def _per_folder_shape(placements):
    """True when ads carry DIFFERENT sets of creatives.

    That difference IS information: it records which landing page actually received
    which dimensions, straight from the folders of the delivery (`970x250_gif` exists
    only for the two lines whose folders had that size). Adding one creative to every
    ad erases it, and no later edit can reconstruct it — so `apply_creative_to_all`
    refuses to add into a tree shaped this way.
    """
    sets = {frozenset(c["name"] for c in (ad.get("creatives") or []))
            for pl in placements for ad in (pl.get("ads") or []) if ad.get("creatives")}
    return len(sets) > 1


def _new_creative(name, lp_name=None, lp_url=None):
    cr = {"name": name, "type": None, "packaged": False, "source_path": None,
          "status": "new"}
    if lp_name:
        cr["lpName"], cr["lpUrl"] = lp_name, lp_url or ""
    return cr


def _full_op(kind, **kw):
    """An op with every schema key present, as apply_ops expects."""
    o = {"op": kind, "placement": None, "ad": None, "creative": None, "name": None,
         "to": None, "lpName": None, "lpUrl": None, "reason": kw.pop("reason", "sugestia agenta (a)")}
    o.update(kw)
    return o


def suggestions_to_ops(proposal, suggestions):
    """Translate role (a)'s SUGGESTIONS into role (b)'s ops.

    Accepting a suggestion then travels the same reviewed, deterministic path as a
    remark — `apply_ops` — instead of a second application engine nobody tested.

    Only what can be applied WITHOUT guessing is translated:
      * `lines` — matched to this order's landing pages BY URL (never by position), then
        the creative name and landing page are set wherever that creative already is.

    Deliberately NOT translated, reported in `notes` instead:
      * `ad_naming` — it asks to SPLIT one ad per file format (160x600 -> _gif/_png/
        _html), which a rename cannot express: the first rename would consume the ad and
        the rest would find no target. The deterministic core does this via
        `fileFormats` in source_map.json, which is where the convention belongs.
      * `group_mappings` — a lasting folder->placement rule belongs in the config
        (promote.py), not in a one-off edit of this one tree.

    Returns (ops, notes).
    """
    ops, notes = [], []
    ours = proposal.get("lines") or ([proposal["line"]] if proposal.get("line") else [])
    by_url = {l.get("url"): l for l in ours if l.get("url")}

    for s in (suggestions or {}).get("lines") or []:
        cur = by_url.get(s.get("lpUrl"))
        if not cur:
            notes.append(f"LP {s.get('lpUrl') or '(brak URL)'} nie należy do tego "
                         f"zlecenia — pominięte")
            continue
        old_cre = cur.get("creativeName")
        new_cre = (s.get("creativeName") or "").strip() or old_cre
        new_lp = (s.get("lpName") or "").strip()
        # only the ads that ALREADY carry this creative. Never `apply_creative_to_all`
        # here: renaming a line and pointing it at its page must not also hand that line
        # dimensions its folder never delivered.
        holders = [(pl["name"], ad["name"])
                   for pl in proposal.get("placements") or []
                   for ad in pl.get("ads") or []
                   if _find(ad.get("creatives") or [], old_cre)]
        if new_cre != old_cre:
            for plc, adn in holders:
                ops.append(_full_op(
                    "rename_creative", placement=plc, ad=adn, creative=old_cre,
                    to=new_cre,
                    reason=f"agent (a): linia {s.get('audience') or new_cre}"))
        if new_lp and new_lp != cur.get("lpName"):
            for plc, adn in holders:
                # the renames above already ran, so address it by its NEW name
                ops.append(_full_op("set_creative_lp", placement=plc, ad=adn,
                                    creative=new_cre, lpName=new_lp,
                                    lpUrl=s.get("lpUrl"),
                                    reason="agent (a): landing page linii"))

    if (suggestions or {}).get("ad_naming"):
        notes.append(f"{len(suggestions['ad_naming'])} sugestii nazw adów pominięto — "
                     f"rozbicie ada na formaty (np. 160x600_gif) robi teraz rdzeń przez "
                     f"`fileFormats` w source_map.json, przemianowanie tego nie wyraża.")
    if (suggestions or {}).get("group_mappings"):
        notes.append(f"{len(suggestions['group_mappings'])} mapowań folderów pominięto — "
                     f"to trwała reguła do configu (promote.py), nie jednorazowa edycja "
                     f"tego drzewa.")
    return ops, notes


def apply_suggestions(proposal, suggestions):
    """Apply the translatable part of role (a)'s suggestions. Returns
    (new_proposal, log, notes) — same log shape as apply_ops, so the UI shows one diff."""
    ops, notes = suggestions_to_ops(proposal, suggestions)
    new_p, log = apply_ops(proposal, ops)
    # keep the order's line metadata in step with the creatives just renamed/relinked,
    # otherwise the header still advertises the old names
    by_url = {s.get("lpUrl"): s for s in (suggestions or {}).get("lines") or []}
    for ln in new_p.get("lines") or []:
        s = by_url.get(ln.get("url"))
        if not s:
            continue
        ln["creativeName"] = (s.get("creativeName") or "").strip() or ln["creativeName"]
        ln["lpName"] = (s.get("lpName") or "").strip() or ln["lpName"]
    if new_p.get("lines"):
        new_p["line"] = new_p["lines"][0]
    return new_p, log, notes


def apply_ops(proposal, ops):
    """Apply agent (b)'s ops to a copy of `proposal`.

    Returns (new_proposal, log) where log is one entry per op:
    {op, ok, detail} — `ok=False` means the op was SKIPPED, never partially applied.
    An op addressing something that no longer exists is skipped and reported, because
    silently doing nothing is how a user ends up trusting a change that never happened.
    """
    p = json.loads(json.dumps(proposal))
    log = []

    def done(o, detail):
        log.append({"op": o.get("op"), "ok": True, "detail": detail})

    def skip(o, why):
        log.append({"op": o.get("op"), "ok": False, "detail": why})

    for o in ops or []:
        kind = o.get("op")
        pls = p.setdefault("placements", [])
        pl = _find(pls, o["placement"]) if o.get("placement") else None

        if kind == "rename_placement":
            target = _find(pls, o.get("placement") or o.get("name"))
            if not target:
                skip(o, f"nie ma placementu {o.get('placement') or o.get('name')!r}")
            elif not o.get("to"):
                skip(o, "brak nowej nazwy (`to`)")
            else:
                old, target["name"] = target["name"], o["to"]
                done(o, f"placement {old!r} -> {o['to']!r}")

        elif kind == "add_placement":
            # tolerate the name arriving in `placement` or `to` — the intent is
            # unambiguous for a create op, and a skipped op means the user's request
            # silently did not happen
            new_name = o.get("name") or o.get("placement") or o.get("to")
            if not new_name:
                skip(o, "brak nazwy nowego placementu")
            elif _find(pls, new_name):
                skip(o, f"placement {new_name!r} już istnieje")
            else:
                pls.append({"name": new_name, "group": None,
                            "source": p.get("source"),
                            "site": (p.get("site") or {}).get("name"),
                            "compatibility": "DISPLAY", "size": "1x1",
                            "status": "new", "ads": []})
                done(o, f"nowy placement {new_name!r}")

        elif kind in ("rename_ad", "add_ad", "delete_ad"):
            if not pl:
                skip(o, f"nie ma placementu {o.get('placement')!r}")
                continue
            if kind == "add_ad":
                new_ad = o.get("name") or o.get("ad") or o.get("to")
                if not new_ad:
                    skip(o, "brak nazwy ada")
                elif _find(pl["ads"], new_ad):
                    skip(o, f"ad {new_ad!r} już jest w {pl['name']!r}")
                else:
                    pl["ads"].append({"name": new_ad, "dimension": new_ad,
                                      "status": "new", "creatives": []})
                    done(o, f"nowy ad {new_ad!r} w {pl['name']!r}")
            else:
                ad = _find(pl["ads"], o.get("ad") or o.get("name"))
                if not ad:
                    skip(o, f"nie ma ada {o.get('ad') or o.get('name')!r} w {pl['name']!r}")
                elif kind == "delete_ad":
                    pl["ads"].remove(ad)
                    done(o, f"usunięto ad {ad['name']!r} z {pl['name']!r}")
                elif not o.get("to"):
                    skip(o, "brak nowej nazwy (`to`)")
                else:
                    old, ad["name"] = ad["name"], o["to"]
                    done(o, f"ad {old!r} -> {o['to']!r} w {pl['name']!r}")

        elif kind == "move_ad":
            src = _find(pls, o.get("placement"))
            dst = _find(pls, o.get("to") or o.get("name"))
            ad = _find(src["ads"], o.get("ad") or o.get("name")) if src else None
            if not src or not dst:
                skip(o, "nie ma placementu źródłowego albo docelowego")
            elif not ad:
                skip(o, f"nie ma ada {o.get('ad') or o.get('name')!r} w {src['name']!r}")
            elif _find(dst["ads"], ad["name"]):
                skip(o, f"ad {ad['name']!r} już jest w {dst['name']!r}")
            else:
                src["ads"].remove(ad)
                dst["ads"].append(ad)
                done(o, f"ad {ad['name']!r}: {src['name']!r} -> {dst['name']!r}")

        elif kind in ("rename_creative", "add_creative", "delete_creative",
                      "set_creative_lp"):
            if not pl:
                skip(o, f"nie ma placementu {o.get('placement')!r}")
                continue
            ad = _find(pl["ads"], o.get("ad"))
            if not ad:
                skip(o, f"nie ma ada {o.get('ad')!r} w {pl['name']!r}")
                continue
            where = f"{pl['name']}/{ad['name']}"
            if kind == "add_creative":
                name = o.get("name") or o.get("creative")
                if not name:
                    skip(o, "brak nazwy creative")
                elif _find(ad["creatives"], name):
                    skip(o, f"creative {name!r} już jest na {where}")
                else:
                    ad["creatives"].append(
                        _new_creative(name, o.get("lpName"), o.get("lpUrl")))
                    done(o, f"creative {name!r} dodany na {where}")
                continue
            cr = _find(ad["creatives"], o.get("creative") or o.get("name"))
            if not cr:
                skip(o, f"nie ma creative {o.get('creative') or o.get('name')!r} na {where}")
            elif kind == "delete_creative":
                ad["creatives"].remove(cr)
                done(o, f"usunięto creative {cr['name']!r} z {where}")
            elif kind == "set_creative_lp":
                if not o.get("lpName"):
                    skip(o, "brak lpName")
                else:
                    cr["lpName"], cr["lpUrl"] = o["lpName"], o.get("lpUrl") or ""
                    done(o, f"creative {cr['name']!r} na {where} -> LP {o['lpName']!r}")
            elif not o.get("to"):
                skip(o, "brak nowej nazwy (`to`)")
            else:
                old, cr["name"] = cr["name"], o["to"]
                done(o, f"creative {old!r} -> {o['to']!r} na {where}")

        elif kind == "rename_creative_all":
            # The op the vocabulary was missing. "Dopisz X do nazwy linii" is a RENAME
            # across the whole tree; without this the model reached for
            # apply_creative_to_all, which ADDS — duplicating every line and then, once
            # the old ones were deleted, leaving every line on every ad. Renaming only
            # where the creative already is cannot flatten anything.
            old = o.get("creative") or o.get("name")
            new = o.get("to")
            if not old or not new:
                skip(o, "podaj `creative` (obecna nazwa) i `to` (nowa nazwa)")
            else:
                lp_name, lp_url = o.get("lpName"), o.get("lpUrl") or ""
                renamed = 0
                for pl_ in pls:
                    for ad in pl_["ads"]:
                        cr = _find(ad["creatives"], old)
                        if not cr or _find(ad["creatives"], new):
                            continue
                        cr["name"] = new
                        if lp_name:
                            cr["lpName"], cr["lpUrl"] = lp_name, lp_url
                        renamed += 1
                if renamed:
                    done(o, f"creative {old!r} -> {new!r} na {renamed} adach"
                            + (f", LP {lp_name!r}" if lp_name else "")
                            + " (nic nie dołożono)")
                else:
                    skip(o, f"nie ma creative {old!r} na żadnym adzie")

        elif kind == "apply_creative_to_all":
            name = o.get("name") or o.get("creative")
            if not name:
                skip(o, "brak nazwy creative")
            else:
                lp_name, lp_url = o.get("lpName"), o.get("lpUrl") or ""
                protect = _per_folder_shape(pls)
                added = relinked = blocked = 0
                for pl_ in pls:
                    for ad in pl_["ads"]:
                        cr = _find(ad["creatives"], name)
                        if cr is None:
                            if protect:
                                blocked += 1     # would erase per-folder assignment
                                continue
                            ad["creatives"].append(_new_creative(name, lp_name, lp_url))
                            added += 1
                        elif lp_name and (cr.get("lpName") != lp_name
                                          or (cr.get("lpUrl") or "") != lp_url):
                            # The creative is already on this ad, so an op carrying a
                            # landing page can only have meant "point it there". Skipping
                            # it made the whole op a no-op that still reported success —
                            # with several LPs in one order every creative already exists
                            # everywhere, so that was every such request.
                            cr["lpName"], cr["lpUrl"] = lp_name, lp_url
                            relinked += 1
                parts = ([f"dodany na {added} adach"] if added else []) + \
                        ([f"LP {lp_name!r} ustawione na {relinked} adach"] if relinked else [])
                guard = (f" NIE dołożono na {blocked} adach: materiały są przypisane "
                         f"według folderów paczki i dołożenie wszędzie zatarłoby to "
                         f"bezpowrotnie. Zmiana nazwy linii to `rename_creative_all`; "
                         f"celowe dołożenie na konkretny ad to `add_creative`."
                         if blocked else "")
                if parts:
                    done(o, f"creative {name!r}: " + ", ".join(parts) + guard)
                else:
                    # never report an untouched tree as an applied change
                    skip(o, (f"creative {name!r}:" + guard) if blocked else
                         f"creative {name!r} jest już na wszystkich adach"
                         + (f" z LP {lp_name!r}" if lp_name else "") + " — nic do zmiany")

        else:
            skip(o, f"nieznana operacja {kind!r}")

    return p, log

