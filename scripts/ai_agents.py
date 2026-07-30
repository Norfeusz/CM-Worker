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
OPS = ["rename_placement", "rename_ad", "rename_creative", "move_ad", "add_placement",
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
  * Landing page name  = "linia{N}-{SOURCE}"      e.g. linia2-GDN, linia1-FB
    The suffix is the SOURCE (GDN/FB/DemGen/...), never the audience. Confirmed against
    live account data.
    When ONE line needs SEVERAL landing pages (different audiences or creative variants
    pointing at different URLs), keep the source suffix last and put the distinguishing
    part in the middle: "linia{N}-{variant}-{SOURCE}"  e.g. linia2-prospecting-GDN.
  * Creative name      = the line, plus the variant/audience when there is more than one
    e.g. linia2, linia2-prospecting, linia4-slonce, refinans-prospecting.
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

Rules:
  * Only emit ops that the remarks actually justify. Do not tidy, reorder, or "improve"
    anything they did not mention.
  * Address nodes by their CURRENT names, exactly as given in the structure.
  * Set every field you do not need to null (the schema requires all keys present).
  * Anything you cannot confidently turn into an op goes into `unclear` as a short question
    in Polish — do not guess it into an op.
  * `apply_creative_to_all` adds (or renames the equivalent of) a creative on every ad; use
    it when the remark says "na wszystkich" / "wszędzie" rather than emitting many ops.
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
            raise AgentError(f"n8n nieosiągalne ({type(e).__name__}: {e})") from e
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
    """Compact view of the current structure + the user's remarks. Deliberately omits
    ids/statuses the agent must not invent decisions from."""
    return {
        "remarks": remarks,
        "answers": answers,
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
                         "guessing."),
    }


# ---- deterministic application of role (b)'s ops ----------------------------
# The agent proposes; this code decides. Every lookup is scoped by placement (and ad)
# rather than by bare name: matching a creative on name alone once renamed the wrong
# neighbouring creative, and that bug must not come back through the AI path.

def _find(seq, name):
    return next((x for x in seq if x["name"] == name), None)


def _new_creative(name, lp_name=None, lp_url=None):
    cr = {"name": name, "type": None, "packaged": False, "source_path": None,
          "status": "new"}
    if lp_name:
        cr["lpName"], cr["lpUrl"] = lp_name, lp_url or ""
    return cr


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
            if not o.get("name"):
                skip(o, "brak nazwy")
            elif _find(pls, o["name"]):
                skip(o, f"placement {o['name']!r} już istnieje")
            else:
                pls.append({"name": o["name"], "group": None,
                            "source": p.get("source"),
                            "site": (p.get("site") or {}).get("name"),
                            "compatibility": "DISPLAY", "size": "1x1",
                            "status": "new", "ads": []})
                done(o, f"nowy placement {o['name']!r}")

        elif kind in ("rename_ad", "add_ad", "delete_ad"):
            if not pl:
                skip(o, f"nie ma placementu {o.get('placement')!r}")
                continue
            if kind == "add_ad":
                if not o.get("name"):
                    skip(o, "brak nazwy ada")
                elif _find(pl["ads"], o["name"]):
                    skip(o, f"ad {o['name']!r} już jest w {pl['name']!r}")
                else:
                    pl["ads"].append({"name": o["name"], "dimension": o["name"],
                                      "status": "new", "creatives": []})
                    done(o, f"nowy ad {o['name']!r} w {pl['name']!r}")
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
            dst = _find(pls, o.get("to"))
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

        elif kind == "apply_creative_to_all":
            name = o.get("name") or o.get("creative")
            if not name:
                skip(o, "brak nazwy creative")
            else:
                touched = 0
                for pl_ in pls:
                    for ad in pl_["ads"]:
                        if not _find(ad["creatives"], name):
                            ad["creatives"].append(
                                _new_creative(name, o.get("lpName"), o.get("lpUrl")))
                            touched += 1
                done(o, f"creative {name!r} dodany na {touched} adach")

        else:
            skip(o, f"nieznana operacja {kind!r}")

    return p, log

