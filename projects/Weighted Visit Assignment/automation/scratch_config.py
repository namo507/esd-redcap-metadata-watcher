"""Copy the lab's config into a throwaway directory for demos and testing.

Driving the board from a browser writes to the config files: the tuning
dropdowns save a weight, a colour match saves a map, and a demo where somebody
changes a number leaves that number changed. Twice during development a
browser session set `weights.phi` to 1.0 in the real `config/engine.json` and
the test suite caught it afterwards; the second time it was only noticed
because a test happened to assert the shipped values.

So a demo should not be pointed at the lab's own config at all. This copies
every file the board writes into `config-scratch/`, which is gitignored, and
prints the environment that points the board there. Change anything you like
during a demo; the lab's numbers are untouched.

Run:  python3 automation/scratch_config.py
      make serve-scratch      # copies, then serves against the copy
"""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "config")
SCRATCH = os.path.join(ROOT, "config-scratch")

#: Every config file the running board can write to, and the variable that
#: redirects it. A file the board only reads does not need copying, but these
#: do: leaving one pointed at config/ is how a demo edits the real thing.
REDIRECTS = {
    "engine.json": "ESD_ENGINE_PATH",
    "roster.json": "ESD_ROSTER_PATH",
    "lab-resources.json": "ESD_RESOURCES_PATH",
    "calendar-colors.json": "ESD_COLOR_MAP_PATH",
    "calendar-roles.json": "ESD_CALENDAR_ROLES_PATH",
}


def build(quiet: bool = False) -> dict:
    """Refresh the scratch copy and return the environment that points at it."""
    os.makedirs(SCRATCH, exist_ok=True)
    env = {}
    for name, var in REDIRECTS.items():
        target = os.path.join(SCRATCH, name)
        source = os.path.join(SOURCE, name)
        if os.path.exists(source):
            shutil.copy2(source, target)
        env[var] = target

    # Everything else the board reads but never writes, copied so the scratch
    # directory is a complete config rather than a partial one that silently
    # falls back to the real files for whatever is missing.
    for name in os.listdir(SOURCE):
        if name.endswith(".json") and name not in REDIRECTS:
            shutil.copy2(os.path.join(SOURCE, name), os.path.join(SCRATCH, name))

    if not quiet:
        print(f"Scratch config in {os.path.relpath(SCRATCH, ROOT)}/")
        print("The board writes here instead of config/. Point it with:")
        print()
        for var, path in env.items():
            print(f"  export {var}={os.path.relpath(path, ROOT)}")
        print()
        print("  make serve-scratch     does all of that and starts the board")
    return env


def main() -> int:
    env = build()
    if "--exec" in sys.argv:
        os.environ.update(env)
        os.environ.setdefault("ESD_DB", os.path.join("data", "scratch.db"))
        os.chdir(ROOT)
        from backend import server
        return server.main([a for a in sys.argv[1:] if a != "--exec"]) or 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
