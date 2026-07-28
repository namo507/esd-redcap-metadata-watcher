"""Study registry and environment configuration for the live dashboard.

Tokens are never hardcoded here. They are read from the repository-root `.env`
file (git-ignored) or from the process environment, which is what a hosted
deployment supplies through its own secret store.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

DEFAULT_API_URL = "https://redcap.research.sc.edu/api/"

# Seconds between outbound REDCap request starts, and how long a cached snapshot
# is served before the next rerun refetches it.
MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("REDCAP_MIN_REQUEST_INTERVAL_SECONDS", "1.25")
)
REFRESH_INTERVAL_SECONDS = int(os.getenv("REDCAP_REFRESH_INTERVAL_SECONDS", "1800"))
RATE_LIMIT_RETRY_SECONDS = float(os.getenv("REDCAP_RATE_LIMIT_RETRY_SECONDS", "15"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REDCAP_REQUEST_TIMEOUT_SECONDS", "180"))


@dataclass(frozen=True)
class StudyDefinition:
    """One REDCap project the dashboard reports on."""

    key: str
    label: str
    token_env: str
    blurb: str = ""

    @property
    def token(self) -> str:
        return (os.environ.get(self.token_env) or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.token)


STUDY_REGISTRY: tuple[StudyDefinition, ...] = (
    StudyDefinition(
        key="NANO",
        label="NANO",
        token_env="NANO_API_TOKEN",
        blurb="NANO Study Surveys",
    ),
    StudyDefinition(
        key="NICO",
        label="NICO",
        token_env="NICO_API_TOKEN",
        blurb="NICO Study",
    ),
    StudyDefinition(
        key="IPSA",
        label="IPSA",
        token_env="IPSA_API_TOKEN",
        blurb="IPSA Study Surveys and Data Entry",
    ),
    StudyDefinition(
        key="ACTION",
        label="ACTION",
        token_env="ACTION_API_TOKEN",
        blurb="ACTION Study",
    ),
)


def load_env_file(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load `KEY=value` pairs from a dotenv file into os.environ.

    Returns the list of keys that were set. Values are never logged or returned.
    Existing environment variables win unless `override` is set, so a hosted
    deployment's own secret store takes precedence over any local file.
    """
    target = Path(path) if path is not None else ENV_PATH
    if not target.is_file():
        return []

    applied: list[str] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied


def api_url() -> str:
    return (os.environ.get("REDCAP_API_URL") or DEFAULT_API_URL).strip()


def configured_studies() -> tuple[StudyDefinition, ...]:
    """Return registry entries whose token is present in the environment."""
    return tuple(study for study in STUDY_REGISTRY if study.configured)


def missing_studies() -> tuple[StudyDefinition, ...]:
    return tuple(study for study in STUDY_REGISTRY if not study.configured)


def iter_studies() -> Iterator[StudyDefinition]:
    yield from STUDY_REGISTRY
