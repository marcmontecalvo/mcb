"""Content-addressed upload of the large edit sets of a plan request.

``body_search.edit_payload`` gives every plan the build-wide ``protected`` cells and
``costly`` walls: thousands of cells that change only when the build or the bot's own
footholds do. Sent inline, every plan_route was 20-31 KB and went out as 3+ synchronous
framed round trips (house-large run 20261119T133618892664Z: ~300 s of blocked ticks).

Terrain-mode plans (BodyTerrainSearch) carry the same build in
``policy.protected_positions`` / ``costly_positions`` and footholds in ``protected``.
Each such list is uploaded once to the body's ``PlanSets`` store (op ``planner_set``:
begin / part / commit; the id is the sha256 of the set's JSON, which the body checks)
and a plan names it as ``edits["<field>_set"]``, keeping inline only the entries added
since that upload. The body resolves base + inline back into the exact list Python
built, so planner results do not change. A set that lost entries is uploaded again
(a reference never protects a cell Python no longer protects), and a reference the
body does not hold is refused (``plan_set_unknown``) rather than planned without it.
"""

import base64
import hashlib
import json
import zlib

FIELDS = ("protected", "costly", "protected_positions", "costly_positions")
# Where the large lists sit: route edits, and the terrain plan's footholds and policy.
PATHS = (
    ("edits", "protected"),
    ("edits", "costly"),
    ("protected",),
    ("policy", "protected_positions"),
    ("policy", "costly_positions"),
)
# Entries a field may carry inline (as a whole list or as the delta over its uploaded
# set). 128 cells is ~2 KB of JSON, so a plan with both fields stays one bridge frame.
INLINE_LIMIT = 128
PART = 10_000


def encode(field, entries):
    """(set id, raw JSON bytes) of one set; the id is the sha256 of the bytes."""
    raw = json.dumps(
        {"field": field, "entries": [list(e) for e in entries]},
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), raw


def frames(set_id, raw):
    """planner_set frames that upload ``raw`` under ``set_id``, in order."""
    encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
    yield {
        "kind": "begin",
        "set_id": set_id,
        "encoded_length": len(encoded),
        "raw_length": len(raw),
        "sha256": set_id,
    }
    for offset in range(0, len(encoded), PART):
        yield {
            "kind": "part",
            "set_id": set_id,
            "offset": offset,
            "data": encoded[offset : offset + PART],
        }
    yield {"kind": "commit", "set_id": set_id}


class PlanSetStore:
    """What this client has uploaded to one body: field -> (set id, entries)."""

    def __init__(self, identity=None):
        self.identity = identity
        self.bases = {}

    def forget(self):
        self.bases.clear()

    def hoist(self, payload, upload):
        """The payload with each large edit set replaced by a reference plus delta.

        ``upload(set_id, raw)`` sends one set; it runs before the reference is used and
        the store records the set only once it returns.
        """
        out = dict(payload)
        for path in PATHS:
            *parents, field = path
            holder = out
            for key in parents:
                holder = holder.get(key) if isinstance(holder, dict) else None
            entries = holder.get(field) if isinstance(holder, dict) else None
            if not entries or len(entries) <= INLINE_LIMIT:
                continue
            current = {tuple(e) for e in entries}
            base = self.bases.get(path)
            if (
                base is None
                or not base[1] <= current
                or len(current - base[1]) > INLINE_LIMIT
            ):
                set_id, raw = encode(field, sorted(current))
                upload(set_id, raw)
                base = self.bases[path] = (set_id, frozenset(current))
            if parents:  # copy the nested object before replacing its list
                holder = out[parents[0]] = dict(holder)
            holder[field] = [list(e) for e in sorted(current - base[1])]
            holder[f"{field}_set"] = base[0]
        return out


def collect(records):
    """set id -> stored set, from logged planner_set request payloads (analysis)."""
    pending, sets = {}, {}
    for p in records:
        set_id, kind = p.get("set_id"), p.get("kind")
        if kind == "begin":
            pending[set_id] = []
        elif kind == "part" and set_id in pending:
            pending[set_id].append((p["offset"], p["data"]))
        elif kind == "commit" and set_id in pending:
            data = "".join(d for _, d in sorted(pending.pop(set_id)))
            raw = zlib.decompress(base64.b64decode(data))
            if hashlib.sha256(raw).hexdigest() == set_id:
                sets[set_id] = json.loads(raw)
    return sets


def resolve(payload, sets):
    """The request as the body plans it: references replaced by base + inline."""

    def one(obj):
        out = {k: v for k, v in obj.items() if not k.endswith("_set")}
        for field in FIELDS:
            set_id = obj.get(f"{field}_set")
            if set_id is None:
                continue
            stored = sets.get(set_id)
            if stored is None or stored.get("field") != field:
                raise KeyError(f"plan_set_unknown: {field} {set_id}")
            out[field] = stored["entries"] + list(obj.get(field, []))
        return out

    out = one(payload)
    for part in ("edits", "policy"):
        if isinstance(out.get(part), dict):
            out[part] = one(out[part])
    return out
