"""The knobs the lab is allowed to turn, and what turning one does.

Every tunable on this board already lived in ``config/*.json``. What was
missing was a way to turn one without editing a file and restarting, which in
practice meant nobody turned them and the numbers stopped being questioned.

Three rules shape this module:

* **A knob is data, not code.** Adding one is an entry in ``KNOBS`` (or a row
  the roster generates). No route, no handler, no frontend change. The page
  renders whatever the catalogue returns, so the two can never drift.
* **A dropdown, not a text box.** Every knob carries its own list of allowed
  values and the server refuses anything outside it. A weight cannot be set to
  "0.45 " with a trailing space, a tech-kit count cannot be negative, and a
  typo cannot reach the engine.
* **Applying is immediate and total.** A change rewrites the config file,
  reloads it in-process and rebuilds nothing else. The uploaded calendar and
  any decisions already made survive, because a scheduler tweaking a weight is
  not asking to lose the week's work.

The weights are the one knob with a consequence worth stating: they must sum
to 1, so setting one rescales the other three in proportion. The catalogue
says so on the control, and the response reports where the other three landed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from esd_scheduler.config import EngineConfig, config_path, load_config
from esd_scheduler.roster import Roster

def _engine_path() -> str:
    # One source of truth for where this lives: reading the env var again here
    # is how the writer and the loader end up pointing at different files.
    return config_path()


def _roster_path() -> str:
    return os.environ.get("ESD_ROSTER_PATH", os.path.join("config", "roster.json"))


def _resources_path() -> str:
    return os.environ.get("ESD_RESOURCES_PATH",
                          os.path.join("config", "lab-resources.json"))


# ---------------------------------------------------------------------------
# Option builders
# ---------------------------------------------------------------------------


def _numbers(values, suffix: str = "") -> List[dict]:
    return [{"value": v, "label": f"{v:g}{suffix}"} for v in values]


def _fractions(step: float = 0.05) -> List[dict]:
    """0 to 1 in steps, labelled as percentages because that is how a share reads."""
    out = []
    n = int(round(1.0 / step))
    for i in range(n + 1):
        v = round(i * step, 4)
        out.append({"value": v, "label": f"{v:.2f}  ({v * 100:.0f}%)"})
    return out


def _choice(pairs) -> List[dict]:
    return [{"value": v, "label": lab} for v, lab in pairs]


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

# group -> (title, one line saying what turning anything in it changes)
GROUPS = {
    "ranking": ("How it ranks",
                "The four criteria the score is made of. They always sum to "
                "1, so raising one lowers the rest."),
    "people": ("Per person",
               "Capacity is the only per-person number that is a judgement "
               "rather than a transcription from the manual."),
    "lab": ("The lab's limits",
            "Physical facts about the lab. These stop a visit happening "
            "whatever it scores."),
}

# Every fixed knob. A new one is a row here and nothing else.
KNOBS: List[dict] = [
    {
        "key": "weights.phi", "group": "ranking", "label": "Continuity",
        "help": "The family has seen this person before.",
        "options": _fractions(), "rescales": True,
    },
    {
        "key": "weights.psi", "group": "ranking", "label": "Burden relief",
        "help": "Spreading the load across the team.",
        "options": _fractions(), "rescales": True,
    },
    {
        "key": "weights.omega", "group": "ranking", "label": "Family preference",
        "help": "Somebody the family asked for, or asked to avoid.",
        "options": _fractions(), "rescales": True,
    },
    {
        "key": "weights.p", "group": "ranking", "label": "Protocol continuity",
        "help": "The same rater as the previous checkpoint.",
        "options": _fractions(), "rescales": True,
    },
    {
        "key": "engine.epsilon_review_band", "group": "ranking",
        "label": "Tie band",
        "help": "Two scores closer than this are reported as a tie rather "
                "than a first and second.",
        "options": _numbers([0.0, 0.01, 0.02, 0.03, 0.05, 0.1]),
    },
    {
        "key": "engine.top_k", "group": "ranking", "label": "Pairings shown",
        "help": "How many ranked pairs the board offers per visit.",
        "options": _numbers([1, 3, 5, 8]),
    },
    {
        "key": "lab.tech_kits.NANO", "group": "lab", "label": "NANO tech kits",
        "help": "The manual says two. A third kit is this number, not a "
                "release.",
        "options": _numbers([1, 2, 3, 4]),
    },
    {
        "key": "lab.friday_closed", "group": "lab", "label": "Fridays",
        "help": "The manual holds Fridays for lab meetings. A Friday visit "
                "can still be taken with a logged override.",
        "options": _choice([(True, "Closed - lab meetings"),
                            (False, "Open for visits")]),
    },
    {
        "key": "lab.working_hours.start_hour", "group": "lab",
        "label": "Day starts",
        "help": "Earlier than this counts as out of hours.",
        "options": _numbers([7, 8, 9, 10], ":00"),
    },
    {
        "key": "lab.working_hours.end_hour", "group": "lab", "label": "Day ends",
        "help": "Later than this counts as out of hours.",
        "options": _numbers([15, 16, 17, 18, 19], ":00"),
    },
    {
        "key": "lab.working_hours.grace_minutes", "group": "lab",
        "label": "Out-of-hours grace",
        "help": "What stops a visit running ten minutes late from counting "
                "as an evening shift.",
        "options": _numbers([0, 15, 30, 45, 60], " min"),
    },
    {
        "key": "lab.screenshot_uploads.image_reader", "group": "lab",
        "label": "Screenshot reader",
        "help": "Geometry alone, or geometry plus a local neural read of each "
                "event's own text. Measured on this lab's prints, the neural "
                "pass changed nothing: the events do not print their times.",
        "options": _choice([("classical", "Geometry (measured)"),
                            ("neural", "Geometry + neural text read")]),
    },
    {
        "key": "lab.screenshot_uploads.auto_confirm", "group": "lab",
        "label": "Screenshot uploads",
        "help": "A PDF is read exactly. A screenshot is measured off pixels, "
                "and a block it misses reads as free time.",
        "options": _choice([(True, "Count immediately"),
                            (False, "Hold for review")]),
    },
]


def _current(key: str, cfg: EngineConfig, roster: Roster, lab: dict) -> Any:
    if key.startswith("weights."):
        return getattr(cfg.weights, key.split(".", 1)[1])
    if key.startswith("engine."):
        return getattr(cfg, key.split(".", 1)[1])
    if key == "lab.friday_closed":
        return 4 in (lab.get("closed_weekdays") or [])
    if key.startswith("lab."):
        node: Any = lab
        for part in key.split(".")[1:]:
            node = (node or {}).get(part)
        return node
    if key.startswith("roster."):
        _, cid, field = key.split(".", 2)
        entry = roster.by_id().get(cid)
        return getattr(entry, field, None) if entry else None
    return None


def _read_lab() -> dict:
    path = _resources_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def catalogue(session) -> dict:
    """Every knob, its current value, and what it may be set to.

    Per-person rows are generated from the roster, so somebody joining the lab
    gets a capacity control with no code change -- the same rule the read
    table's dropdown follows.
    """
    cfg = session.cfg
    roster = session.roster_config
    lab = _read_lab()

    rows: List[dict] = []
    for knob in KNOBS:
        value = _current(knob["key"], cfg, roster, lab)
        rows.append({**knob, "value": value,
                     "options": _with_current(knob["options"], value)})

    # Capacity, one row per person the board schedules.
    hours = [5, 10, 15, 20, 25, 30, 35, 40]
    for entry in roster.active:
        key = f"roster.{entry.id}.capacity_hours_week"
        value = entry.capacity_hours_week
        rows.append({
            "key": key, "group": "people",
            "label": entry.name,
            "alias": (entry.manual_name
                      if entry.manual_name
                      and entry.manual_name.lower() not in entry.name.lower()
                      else None),
            "help": "Hours a week this person is available for visits.",
            "options": _with_current(_numbers(hours, " h/week"), value),
            "value": value,
        })

    return {
        "groups": [{"id": gid, "title": title, "note": note}
                   for gid, (title, note) in GROUPS.items()],
        "knobs": rows,
        "weight_vector_id": cfg.weight_vector_id,
    }


def _with_current(options: List[dict], value: Any) -> List[dict]:
    """Make sure the value in the file is always one of the choices.

    A config set by hand can hold a number nobody put on the list. Dropping it
    from the options would make the control display a different value from the
    one actually in force, which is the one thing a settings page must never
    do. So it is added rather than hidden.
    """
    if value is None:
        return options
    for opt in options:
        if _same(opt["value"], value):
            return options
    # Round the label, never the value. A rescaled weight is 0.163636..., and
    # "0.164 (in force)" reads as a number while the option still carries the
    # exact one. Snapping the value to a listed step instead would break the
    # sum-to-1 invariant the rescale exists to keep.
    if isinstance(value, bool):
        label = "on" if value else "off"
    elif isinstance(value, float):
        label = f"{value:.3f}".rstrip("0").rstrip(".") or "0"
    else:
        label = str(value)
    return sorted(options + [{"value": value, "label": f"{label}  (in force)"}],
                  key=lambda o: (not isinstance(o["value"], (int, float)),
                                 o["value"] if isinstance(o["value"], (int, float))
                                 else str(o["value"])))


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


# ---------------------------------------------------------------------------
# Applying a change
# ---------------------------------------------------------------------------


class SettingError(ValueError):
    """A change the board will not make, with a reason a person can act on."""


def _find(key: str, session) -> Optional[dict]:
    for row in catalogue(session)["knobs"]:
        if row["key"] == key:
            return row
    return None


def apply(session, key: str, value: Any) -> dict:
    """Set one knob, write it, reload it, and say what moved.

    Returns the changed keys and their new values -- including the ones the
    caller did not ask for, because setting a weight moves the other three and
    a control that hid that would be lying about what it just did.
    """
    row = _find(key, session)
    if row is None:
        raise SettingError(f"there is no setting called {key!r}")

    allowed = [o["value"] for o in row["options"]]
    if not any(_same(v, value) for v in allowed):
        raise SettingError(
            f"{value!r} is not one of the values {key!r} accepts. This board "
            f"only accepts values it offered, so a typo cannot reach the "
            f"engine.")
    # Use the option's own value, so 2 and 2.0 do not both end up in the file.
    value = next(v for v in allowed if _same(v, value))

    if key.startswith("weights."):
        changed = _set_weight(key.split(".", 1)[1], float(value))
    elif key.startswith("engine."):
        changed = _set_engine(key.split(".", 1)[1], value)
    elif key.startswith("lab."):
        changed = _set_lab(key, value)
    elif key.startswith("roster."):
        changed = _set_roster(key, value)
    else:                                    # unreachable: _find would be None
        raise SettingError(f"nothing knows how to set {key!r}")

    session.reload_settings()
    return changed


def _set_weight(name: str, target: float) -> dict:
    """Set one weight and rescale the other three so the four still sum to 1.

    The engine refuses a set that does not sum to 1, and rightly: a score built
    from weights summing to 1.2 is not on the same scale as any decision
    already recorded. Rescaling the others in proportion is the only move that
    keeps the invariant while changing exactly the one thing that was asked
    for.
    """
    cfg = load_config(_engine_path())
    current = cfg.weights.as_dict()
    if name not in current:
        raise SettingError(f"there is no weight called {name!r}")
    cfg.weights = cfg.weights.perturbed(name, target - current[name])
    # Rounding four rescaled floats can leave the sum a hair off 1. Put any
    # residue on the largest of the others, where it is proportionally
    # smallest, rather than letting validate() reject the write.
    values = {k: round(v, 6) for k, v in cfg.weights.as_dict().items()}
    residue = round(1.0 - sum(values.values()), 6)
    if residue:
        others = sorted((k for k in values if k != name),
                        key=lambda k: values[k], reverse=True)
        if others:
            values[others[0]] = round(values[others[0]] + residue, 6)
    cfg.weights = type(cfg.weights)(**values)
    cfg.validate()
    cfg.save(_engine_path())
    return {f"weights.{k}": v for k, v in values.items()}


def _set_engine(field: str, value: Any) -> dict:
    cfg = load_config(_engine_path())
    if not hasattr(cfg, field):
        raise SettingError(f"the engine has no setting called {field!r}")
    setattr(cfg, field, value)
    cfg.validate()
    cfg.save(_engine_path())
    return {f"engine.{field}": value}


def _set_lab(key: str, value: Any) -> dict:
    """Edit lab-resources.json in place, keeping the comments in it.

    That file explains itself in ``_comment`` blocks -- what came from the
    manual and what the lab still has to fill in. Rewriting it from a parsed
    object would throw those away, so the raw JSON is loaded, one field is
    changed, and the rest is written back untouched.
    """
    raw = _read_lab()
    if key == "lab.friday_closed":
        days = [d for d in (raw.get("closed_weekdays") or []) if d != 4]
        if value:
            days.append(4)
        raw["closed_weekdays"] = sorted(days)
    else:
        parts = key.split(".")[1:]
        node = raw
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    _write_json(_resources_path(), raw)
    return {key: value}


def _set_roster(key: str, value: Any) -> dict:
    _, cid, field = key.split(".", 2)
    path = _roster_path()
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    for entry in raw.get("coordinators", []):
        if entry.get("id") == cid:
            entry[field] = value
            break
    else:
        raise SettingError(f"nobody on the roster has the id {cid!r}")
    _write_json(path, raw)
    return {key: value}


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
