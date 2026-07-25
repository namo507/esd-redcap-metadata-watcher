from __future__ import annotations

import datetime as dt
from pathlib import Path

import recruitment_reports as reports


def _snapshot(project_key: str, report_date: dt.date) -> dict[str, object]:
    config = reports.PROJECT_CONFIG[project_key]
    milestones = reports.build_milestones(config)
    current_index = next(
        index for index, milestone in enumerate(milestones) if milestone >= report_date
    )
    completed = [index for index, milestone in enumerate(milestones) if milestone < report_date]
    latest_completed_index = completed[-1] if completed else -1
    live = (
        {"Total": 219, "Minority": 99, "Hispanic": 16}
        if project_key == "NANO"
        else {"Total": 82, "Minority": 53, "Hispanic": 14}
    )
    return {
        "milestones": milestones,
        "current_index": current_index,
        "latest_completed_index": latest_completed_index,
        "live": live,
    }


def test_build_milestones_matches_tri_yearly_schedule() -> None:
    milestones = reports.build_milestones(reports.PROJECT_CONFIG["NANO"])

    assert len(milestones) == 14
    assert milestones[0] == dt.date(2024, 8, 1)
    assert milestones[-1] == dt.date(2028, 12, 1)


def test_ratio_and_status_handle_pending_and_missing_targets() -> None:
    assert reports.ratio_pct(25, 10) == "250%"
    assert reports.ratio_pct(None, 10) == "N/A"
    assert reports.ratio_pct(25, 0) == "N/A"

    assert reports.status_text(None, 10, 2, 2) == reports.STATUS_PENDING
    assert reports.status_text(11, 10, 1, 2) == reports.STATUS_ON_TARGET
    assert reports.status_text(9, 10, 1, 2) == reports.STATUS_BEHIND
    assert reports.status_text(9, None, 1, 2) == "N/A"


def test_render_project_table_uses_reference_layout_labels() -> None:
    report_date = dt.date(2026, 7, 24)
    html = reports.render_project_table(
        "NANO",
        _snapshot("NANO", report_date),
        reports.PROJECT_CONFIG["NANO"],
        report_date,
    )

    assert "Recruitment Milestones for MH132925 - The Role of Autonomic Regulation of Attention in the Emergence of ASD" in html
    assert "Tri Yearly Milestones" in html
    assert "Actual/Target Ratio: Total Recruitment" in html
    assert "Reference targets and published actuals are reproduced unchanged" in html
    assert 'class="month current">Aug 1<' in html
    assert 'class="value curr current">150<' in html


def test_render_dashboard_contains_both_project_tables() -> None:
    report_date = dt.date(2026, 7, 24)
    rendered_tables = {
        project_key: reports.render_project_table(
            project_key,
            _snapshot(project_key, report_date),
            reports.PROJECT_CONFIG[project_key],
            report_date,
        )
        for project_key in ("NANO", "NICO")
    }

    html = reports.render_dashboard_html(
        rendered_tables, report_date, reports.DEFAULT_API_URL
    )

    assert html.count('class="report-card"') == 2
    assert "Read-only REDCap validation for NANO and NICO" in html
    assert "Recruitment Milestones for NICO Study" in html


def test_build_combined_dashboard_extracts_table_body_from_documents() -> None:
    report_date = dt.date(2026, 7, 24)
    rendered_table = reports.render_project_table(
        "NANO",
        _snapshot("NANO", report_date),
        reports.PROJECT_CONFIG["NANO"],
        report_date,
    )
    document = reports.render_dashboard_html(
        {"NANO": rendered_table}, report_date, reports.DEFAULT_API_URL
    )

    combined = reports.build_combined_dashboard_from_documents(
        {"NANO": document, "NICO": document},
        report_date,
        reports.DEFAULT_API_URL,
    )

    assert combined.count('class="report-card"') == 2
    assert combined.count("Recruitment Milestones for MH132925 - The Role of Autonomic Regulation of Attention in the Emergence of ASD") == 2


def test_archive_existing_dated_outputs_keeps_latest_aliases(tmp_path: Path) -> None:
    output_root = tmp_path / "recruitment_outputs"
    archive_root = output_root / reports.ARCHIVE_SUBDIR
    output_root.mkdir(parents=True)

    dated_html = output_root / "nano_recruitment_milestones_2026-07-24.html"
    dated_xlsx = output_root / "recruitment_milestones_2026-07-24.xlsx"
    latest_html = output_root / "nano_recruitment_milestones_latest.html"
    latest_xlsx = output_root / "recruitment_milestones_latest.xlsx"
    dated_html.write_text("dated-html", encoding="utf-8")
    dated_xlsx.write_bytes(b"dated-xlsx")
    latest_html.write_text("latest-html", encoding="utf-8")
    latest_xlsx.write_bytes(b"latest-xlsx")

    archived = reports._archive_existing_dated_outputs(output_root, archive_root)

    assert sorted(path.name for path in archived) == sorted(
        [dated_html.name, dated_xlsx.name]
    )
    assert not dated_html.exists()
    assert not dated_xlsx.exists()
    assert (archive_root / dated_html.name).read_text(encoding="utf-8") == "dated-html"
    assert (archive_root / dated_xlsx.name).read_bytes() == b"dated-xlsx"
    assert latest_html.read_text(encoding="utf-8") == "latest-html"
    assert latest_xlsx.read_bytes() == b"latest-xlsx"
