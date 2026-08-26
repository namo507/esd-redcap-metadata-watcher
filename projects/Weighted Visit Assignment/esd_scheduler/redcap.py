"""The one external service this board talks to: the NANO study's REDCap.

Everything else here is deliberately offline -- calendars are read from files
the lab prints, and the scoring runs in-process. REDCap is the exception,
because the participants and their visit windows live there and nowhere else,
and a board that invents family IDs is a demo rather than a tool.

THREE RULES, AND THEY ARE THE WHOLE DESIGN.

**The token is never in this repository.** It lives in `config/redcap.env`,
which is gitignored, or in the environment. A token is a bearer credential for
a study with human participants: whoever holds it can read the record set. If
one has ever been pasted into a chat window, an email, or a commit, it is
compromised and the only fix is to regenerate it in REDCap.

**Only what scheduling needs is fetched.** The board needs a participant ID, a
protocol time point and the dates its window opens and closes. It does not
need a name, a date of birth, a contact detail or an assessment score, so the
export names its fields explicitly and never asks for a whole record. What is
not requested cannot be leaked by a later bug.

**Nothing fetched is ever written where git can see it.** Responses land in
`data/redcap/`, which is gitignored, and the board reads them from there.

The REDCap API is a single POST endpoint that takes a form-encoded body and
returns JSON. That is simple enough to call with the standard library, so
this adds no dependency -- and a dependency that carries a study token is one
worth not having.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

DEFAULT_URL = "https://redcap.research.sc.edu/api/"
ENV_PATH = os.path.join("config", "redcap.env")
CACHE_DIR = os.path.join("data", "redcap")

#: The manual's rule for a NANO participant ID: four digits starting with 5.
NANO_ID = re.compile(r"^5\d{3}$")


class RedcapError(RuntimeError):
    """Something went wrong that a person has to fix, with a readable reason."""


@dataclass
class RedcapConfig:
    token: str = ""
    url: str = DEFAULT_URL
    #: Which REDCap fields hold the id, the time point and the window. Named
    #: here rather than guessed, because a wrong guess silently schedules
    #: against the wrong dates.
    #: Checked against the project's data dictionary rather than guessed.
    #: pid 4218 keeps the record id in `demo_id`, the anchor dates in
    #: `demo_dob` and `demo_duedate`, and the arm in the `demo_status`
    #: checkbox (1 TD Sib, 2 ASD Sib, 3 PT, 4 Other).
    id_field: str = "demo_id"
    timepoint_field: str = "redcap_event_name"
    dob_field: str = "demo_dob"
    due_date_field: str = "demo_duedate"
    status_field: str = "demo_status"
    unenrolled_field: str = "demo_unenrolled"
    source: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.token)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "RedcapConfig":
        """Token from the environment first, then the gitignored env file."""
        cfg = cls()
        token = os.environ.get("REDCAP_TOKEN", "").strip()
        if token:
            cfg.token, cfg.source = token, "the REDCAP_TOKEN environment variable"
        cfg.url = os.environ.get("REDCAP_URL", cfg.url).strip() or DEFAULT_URL

        path = path or os.environ.get("ESD_REDCAP_ENV", ENV_PATH)
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip().upper(), value.strip().strip('"\'')
                if key == "REDCAP_TOKEN" and not cfg.token:
                    cfg.token, cfg.source = value, path
                elif key == "REDCAP_URL" and value:
                    cfg.url = value
                elif key == "REDCAP_ID_FIELD" and value:
                    cfg.id_field = value
                elif key == "REDCAP_TIMEPOINT_FIELD" and value:
                    cfg.timepoint_field = value
                elif key == "REDCAP_DOB_FIELD" and value:
                    cfg.dob_field = value
                elif key == "REDCAP_DUE_DATE_FIELD" and value:
                    cfg.due_date_field = value
                elif key == "REDCAP_STATUS_FIELD" and value:
                    cfg.status_field = value
        return cfg


def _post(cfg: RedcapConfig, payload: Dict[str, str], timeout: float = 30.0):
    """One form-encoded POST. The token is added here and logged nowhere."""
    if not cfg.configured:
        raise RedcapError(
            "No REDCap token. Put REDCAP_TOKEN=... in config/redcap.env, which "
            "is gitignored, or set it in the environment. Never commit it.")
    body = urllib.parse.urlencode(
        {**payload, "token": cfg.token, "format": "json",
         "returnFormat": "json"}).encode()
    request = urllib.request.Request(cfg.url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # REDCap explains a refusal in the response body, and that body does
        # not contain the token -- the token is in the *request*. Swallowing
        # the explanation left "HTTPError" and nothing to act on, which is
        # exactly the message a person cannot use.
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                      # noqa: BLE001
            detail = ""
        raise RedcapError(
            f"REDCap refused the request (HTTP {exc.code}): "
            f"{detail or exc.reason}")
    except Exception as exc:                                   # noqa: BLE001
        # Never re-raise the request object: it carries the token in its body.
        raise RedcapError(f"REDCap could not be reached: {type(exc).__name__}")
    try:
        data = json.loads(raw)
    except ValueError:
        raise RedcapError(f"REDCap returned something that is not JSON: "
                          f"{raw[:120]}")
    if isinstance(data, dict) and data.get("error"):
        raise RedcapError(f"REDCap refused the request: {data['error']}")
    return data


def project_info(cfg: Optional[RedcapConfig] = None) -> dict:
    """The project's own description. Carries no participant data at all.

    This is the call worth making first: it proves the token works and the
    study is the one expected, without reading a single record.
    """
    cfg = cfg or RedcapConfig.load()
    info = _post(cfg, {"content": "project"})
    return {
        "project_id": info.get("project_id"),
        "project_title": info.get("project_title"),
        "is_longitudinal": bool(info.get("is_longitudinal")),
        "record_autonumbering": bool(info.get("record_autonumbering_enabled")),
        "purpose": info.get("purpose_other") or info.get("purpose"),
    }


#: REDCap's checkbox codes for the study arm, mapped to what the protocol
#: schedule asks about. The distinction is not cosmetic: a preterm baby's
#: 1m to 24m visits count from the expected due date and a term baby's count
#: from the birthday, so getting this wrong dates every early visit wrong.
STATUS_BY_CODE = {"1": "TD", "2": "ASIB", "3": "PT", "4": "TD"}


@dataclass
class NanoFamily:
    """One participant, carrying only what dating a visit requires.

    No name, no contact detail, no assessment score. The board needs an id, an
    arm and the two anchor dates, and nothing it never fetches can leak.
    """
    family_id: str
    participant_status: str = "TD"
    birth_date: Optional[str] = None
    due_date: Optional[str] = None
    unenrolled: bool = False
    completed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"family_id": self.family_id,
                "participant_status": self.participant_status,
                "birth_date": self.birth_date, "due_date": self.due_date,
                "unenrolled": self.unenrolled,
                "completed": sorted(self.completed, key=_months),
                "protocol": "NANO"}


def _months(checkpoint: str) -> int:
    match = re.match(r"(\d+)", checkpoint or "")
    return int(match.group(1)) if match else 0


def fetch_families(cfg: Optional[RedcapConfig] = None,
                   cache: bool = True) -> List[NanoFamily]:
    """Every enrolled NANO participant, with the dates a window is built from.

    The field list is explicit and short. Asking REDCap for whole records
    would return names, medical history and assessment scores this board has
    no business holding.

    A record is skipped when its id is not a NANO id -- the manual's rule,
    four digits starting with five -- because an id from another arm or a test
    record would otherwise become a family the board tries to schedule.
    """
    cfg = cfg or RedcapConfig.load()
    fields = [cfg.id_field, cfg.dob_field, cfg.due_date_field,
              cfg.status_field, cfg.unenrolled_field,
              # Which checkpoints already happened. Without it the board reads
              # every family as never seen and every visit as their first.
              "visit_date"]
    query = {"content": "record", "type": "flat", "rawOrLabel": "raw",
             "exportSurveyFields": "false", "exportDataAccessGroups": "false"}
    for i, name in enumerate(fields):
        query[f"fields[{i}]"] = name
    rows = _post(cfg, query)
    if not isinstance(rows, list):
        raise RedcapError("REDCap returned no record list.")

    families: Dict[str, NanoFamily] = {}
    for row in rows:
        fid = str(row.get(cfg.id_field, "")).strip()
        if not NANO_ID.match(fid):
            continue
        fam = families.get(fid)
        if fam is None:
            fam = families[fid] = NanoFamily(family_id=fid)

        # A longitudinal export gives one row per event, and the demographics
        # form is only filled at one of them. So take each value from whatever
        # row actually carries it rather than from the first row seen.
        dob = _iso(row.get(cfg.dob_field))
        if dob:
            fam.birth_date = dob
        due = _iso(row.get(cfg.due_date_field))
        if due:
            fam.due_date = due
        if str(row.get(cfg.unenrolled_field, "")).strip() == "1":
            fam.unenrolled = True
        for code, label in STATUS_BY_CODE.items():
            if str(row.get(f"{cfg.status_field}___{code}", "")).strip() == "1":
                fam.participant_status = label

        # An event with a visit date recorded is a checkpoint already done.
        # The board uses that to tell a first visit from an overdue one.
        checkpoint = _checkpoint_from_event(str(row.get(cfg.timepoint_field, "")))
        if checkpoint and str(row.get("visit_date", "")).strip():
            if checkpoint not in fam.completed:
                fam.completed.append(checkpoint)

    out = [f for f in families.values() if not f.unenrolled]
    if cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, "nano-families.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": datetime.now().isoformat(timespec="seconds"),
                       "project": "NANO", "count": len(out),
                       "families": [f.to_dict() for f in out]}, fh, indent=2)
        os.chmod(path, 0o600)
    return out


def cached_families() -> dict:
    """Whatever the last sync wrote, or an empty answer with a reason.

    The board reads this rather than calling REDCap on every request: a
    scheduling screen that hits a study API on each page load is both slow and
    a good way to have the token rate-limited.
    """
    path = os.path.join(CACHE_DIR, "nano-families.json")
    if not os.path.exists(path):
        return {"fetched_at": None, "families": [],
                "reason": "no REDCap sync on file yet; run `make redcap-sync`"}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


#: REDCap event names separate words with underscores as often as not, so
#: "36_month_arm_1" and "visit_9m_arm_1" both have to read as a time point.
#: The lookahead is a letter class rather than \b, because an underscore is a
#: word character and \b therefore never fires between "9m" and "_arm_1".
_EVENT_MONTHS = re.compile(r"(\d+)[\s_-]*(?:months?|mo|m)(?![a-z])", re.I)


def _checkpoint_from_event(event: str) -> str:
    """"visit_9m_arm_1" -> "9m". Anything unreadable comes back empty.

    Empty rather than guessed: a checkpoint decides which assessments a visit
    needs and therefore who may run it, so a wrong one staffs the visit wrong.
    """
    match = _EVENT_MONTHS.search(event or "")
    return f"{int(match.group(1))}m" if match else ""


def _iso(value) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    for form in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, form).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# From a participant record to a visit window
# ---------------------------------------------------------------------------


class _Anchorable:
    """The three fields ``anchor_for`` asks a family about.

    A small stand-in rather than the full Family model: this is the boundary
    between a REDCap record and the board, and building a Family here would
    mean this module knowing about visit history, preferences and travel that
    a record export does not contain.
    """

    def __init__(self, fam: "NanoFamily"):
        self.participant_status = fam.participant_status
        self.birth_date = _as_date(fam.birth_date)
        self.due_date = _as_date(fam.due_date)
        self.anchor_date = self.birth_date


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def visit_windows(fam: "NanoFamily", today: Optional[date] = None) -> List[dict]:
    """Every NANO checkpoint for one family, with the window it may be booked in.

    The dates are not in REDCap and should not be: the manual decides them,
    `config/protocol-schedule.json` transcribes the manual, and the anchor rule
    -- due date for a preterm baby's early visits, birthday otherwise and
    always at 36m -- lives in `schedule.anchor_for`. This only joins the two,
    so changing the protocol changes the board without touching this file.
    """
    from .schedule import ProtocolSchedule, anchor_for

    today = today or date.today()
    checkpoints = ProtocolSchedule.load().checkpoints.get("NANO") or []
    if not checkpoints:
        return []

    subject = _Anchorable(fam)
    done = set(fam.completed)
    out: List[dict] = []
    for checkpoint in checkpoints:
        anchor = anchor_for(subject, checkpoint.name)
        if anchor is None:
            # No anchor is not a window of unknown length; it is no window at
            # all. Offering one would be a date invented by this board.
            out.append({"checkpoint": checkpoint.name, "ideal": None,
                        "window_start": None, "window_end": None,
                        "status": "no anchor date on file",
                        "done": checkpoint.name in done, "days_until": None})
            continue
        start, end = checkpoint.window(anchor)
        ideal = checkpoint.target(anchor)
        if checkpoint.name in done:
            state = "done"
        elif end < today:
            state = "missed"
        elif start > today:
            state = "upcoming"
        else:
            state = "open"
        out.append({
            "checkpoint": checkpoint.name,
            "ideal": ideal.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "status": state,
            "done": checkpoint.name in done,
            "days_until": (start - today).days,
            "remote": bool(getattr(checkpoint, "remote", False)),
        })
    return out


def next_window(fam: "NanoFamily", today: Optional[date] = None) -> Optional[dict]:
    """The checkpoint this family is due for, or None when none is live.

    "Open now" beats "opens soon", and a missed window beats both -- a visit
    whose window has closed is the one somebody needs to be told about, not
    the one three months out.
    """
    windows = [w for w in visit_windows(fam, today) if not w["done"]]
    for state in ("open", "missed", "upcoming"):
        matching = [w for w in windows if w["status"] == state]
        if matching:
            return sorted(matching, key=lambda w: w["window_start"] or "")[0]
    return None
