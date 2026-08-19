"""Offline checks that keep REDCap credentials out of tracked repository files."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from zipfile import BadZipFile, ZipFile, is_zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REDCAP_TOKEN_PATTERN = re.compile(
    rb"(?<![0-9A-Fa-f])[A-Fa-f0-9]{32}(?![0-9A-Fa-f])"
)


def _git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *arguments),
        cwd=REPOSITORY_ROOT,
        check=check,
        capture_output=True,
    )


def _tracked_paths() -> list[Path]:
    result = _git("ls-files", "-z")
    return [
        Path(raw_path.decode("utf-8"))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    ]


def _credential_findings() -> list[str]:
    findings: list[str] = []

    def scan(data: bytes, location: str) -> None:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return
        for match in REDCAP_TOKEN_PATTERN.finditer(data):
            line_number = data.count(b"\n", 0, match.start()) + 1
            findings.append(f"{location}:{line_number}")

    for relative_path in _tracked_paths():
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            continue

        if is_zipfile(path):
            try:
                with ZipFile(path) as archive:
                    for member in archive.infolist():
                        if member.is_dir() or member.file_size > 10_000_000:
                            continue
                        scan(
                            archive.read(member),
                            f"{relative_path.as_posix()}!{member.filename}",
                        )
            except (BadZipFile, OSError):
                pass
            continue

        scan(path.read_bytes(), relative_path.as_posix())
    return findings


def test_tracked_files_do_not_contain_redcap_credentials() -> None:
    findings = _credential_findings()
    assert not findings, (
        "REDCap-like 32-character credentials found in tracked text files:\n"
        + "\n".join(findings)
    )


def test_local_secret_locations_are_untracked_and_ignored() -> None:
    tracked = {path.as_posix() for path in _tracked_paths()}
    assert ".env" not in tracked
    assert not any(path.startswith("recruitment_audit_secure/") for path in tracked)

    for ignored_probe in (
        ".env",
        "recruitment_audit_secure/.secret-hygiene-probe",
    ):
        result = _git("check-ignore", "-q", ignored_probe, check=False)
        assert result.returncode == 0, f"{ignored_probe} must remain git-ignored"
