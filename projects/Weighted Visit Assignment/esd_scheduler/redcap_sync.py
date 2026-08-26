"""`make redcap-sync`: pull the NANO participants into the local cache.

Deliberately a separate entry point rather than something the board does on
start. A scheduling screen should not depend on a study API being reachable,
and a token should be used by a command somebody ran, not by a long-running
process serving a browser.

Prints counts and never a participant's details. A sync that dumped ids and
dates into a terminal would put them in a scrollback buffer, a screenshot and
eventually a ticket.
"""

from __future__ import annotations

import sys
from collections import Counter

from .redcap import RedcapConfig, RedcapError, fetch_families, next_window, project_info


def main(argv=None) -> int:
    cfg = RedcapConfig.load()
    if not cfg.configured:
        print("No REDCap token. Put REDCAP_TOKEN=... in config/redcap.env,")
        print("which is gitignored. Never commit it.")
        return 1
    try:
        info = project_info(cfg)
        print(f"Connected to {info['project_title']} (pid {info['project_id']}), "
              f"token from {cfg.source}.")
        families = fetch_families(cfg)
    except RedcapError as exc:
        print(f"Sync failed: {exc}")
        return 1

    arms = Counter(f.participant_status for f in families)
    states = Counter()
    for fam in families:
        window = next_window(fam)
        states[window["status"] if window else "none"] += 1

    print(f"{len(families)} enrolled participants with a NANO id.")
    print(f"  by arm:   {dict(arms)}")
    print(f"  next window: {dict(states)}")
    print(f"  anchor dates: {sum(1 for f in families if f.birth_date)} birth, "
          f"{sum(1 for f in families if f.due_date)} due")
    print()
    print("Written to data/redcap/nano-families.json, which is gitignored.")
    print("No participant id or date has been printed here on purpose.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
