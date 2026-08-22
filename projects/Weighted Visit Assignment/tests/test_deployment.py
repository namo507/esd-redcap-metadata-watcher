"""Tests for the things a container needs that a laptop does not.

Each of these failed silently at least once while this was being set up, and
each fails in a way that looks fine locally: the server starts, the logs look
healthy, and nothing outside the container can reach it.

Run:  python3 tests/test_deployment.py
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# --- the container has to be reachable --------------------------------------


def test_the_bind_address_can_be_set_from_the_environment():
    """Binding 127.0.0.1 in a container accepts nothing from outside it."""
    from backend import server

    expect(hasattr(server, "build_server"), "build_server went missing")
    src = _read("backend", "server.py")
    expect("ESD_BIND" in src, "there is no way to set the bind address")
    expect('"127.0.0.1"' in src,
           "loopback should stay the local default; only a container overrides it")


def test_the_port_follows_the_environment():
    """Every hosting platform hands the port over in PORT."""
    src = _read("backend", "server.py")
    expect('os.environ.get("PORT")' in src, "the server ignores PORT")
    expect("default=int(os.environ.get(\"PORT\") or 8765)" in src.replace("'", '"'),
           "PORT should be the default with 8765 as the fallback, so the local "
           "command line is unchanged")


def test_the_database_location_follows_the_environment():
    """A volume mount is pointless if the path is hardcoded."""
    src = _read("backend", "server.py")
    expect("ESD_DATA_DIR" in src, "the database path cannot be moved")


def test_every_api_response_allows_a_cross_origin_page():
    """The page and the API are on different hosts once deployed."""
    src = _read("backend", "server.py")
    expect("Access-Control-Allow-Origin" in src, "no CORS header is ever sent")
    expect("do_OPTIONS" in src,
           "without a preflight handler every cross-origin POST fails")


# --- the image has to describe the same server ------------------------------


def test_the_dockerfile_matches_the_flags_the_server_defines():
    """A CMD referring to a flag that does not exist fails only at deploy."""
    docker = _read("Dockerfile")
    src = _read("backend", "server.py")
    for flag in re.findall(r'"(--[a-z-]+)"', docker):
        expect(f'"{flag}"' in src or f"'{flag}'" in src,
               f"the Dockerfile passes {flag}, which server.py does not define")


def test_the_container_binds_every_interface_and_a_known_port():
    docker = _read("Dockerfile")
    expect("ESD_BIND=0.0.0.0" in docker, "the container would bind loopback")
    expect("EXPOSE" in docker, "no port is exposed")
    exposed = re.search(r"EXPOSE (\d+)", docker).group(1)
    expect(f"PORT={exposed}" in docker,
           f"EXPOSE {exposed} does not match the PORT the server will use")


def test_the_health_check_points_at_a_route_that_exists():
    src = _read("backend", "server.py")
    for config, name in ((_read("fly.toml"), "fly.toml"),
                         (_read("..", "..", "render.yaml"), "render.yaml")):
        for path in re.findall(r'(?:path|healthCheckPath):?\s*=?\s*"?(/api/[\w/]+)"?',
                               config):
            expect(f'@get("{path}")' in src,
                   f"{name} health-checks {path}, which the server does not serve")


def test_only_what_the_server_imports_is_required_to_boot():
    """The core list is small because the heavy imports are all lazy."""
    core = _read("requirements-core.txt")
    packages = [l.split(">=")[0].strip() for l in core.splitlines()
                if l.strip() and not l.startswith("#")]
    expect(packages == ["PyMuPDF"],
           f"requirements-core should be PyMuPDF alone, found {packages}")


# --- the page has to survive being hosted somewhere else --------------------


def test_the_api_base_is_configurable():
    core = _read("frontend", "js", "core.js")
    expect("API_BASE" in core and "apiUrl" in core,
           "the frontend still assumes the API is same-origin")
    expect("fetch(apiUrl(" in core or "apiUrl(path)" in core,
           "the fetch helper does not use the configurable base")
    boot = _read("frontend", "js", "boot.js")
    expect("apiUrl(" in boot,
           "the health probe still hits a same-origin path, so the page would "
           "always fall back to the snapshot when hosted separately")


def test_an_empty_api_base_means_same_origin():
    """`make serve` must keep working with no configuration at all."""
    config = _read("frontend", "config.js")
    expect(re.search(r'API_BASE:\s*""', config),
           "the shipped config should be empty so local use is unchanged")


def test_the_page_uses_no_root_relative_paths():
    """Hosted under /<repo>/visitboard/, a root-relative path resolves wrong."""
    html = _read("frontend", "index.html")
    bad = re.findall(r'(?:src|href)="(/[^/][^"]*)"', html)
    expect(not bad, f"these would 404 under a project subpath: {bad}")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
