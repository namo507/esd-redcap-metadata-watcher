# Archive

Superseded material kept for reference. Nothing here is on an active code path — no
script, notebook, workflow, or test reads from this directory.

| Path | What it is | Superseded by |
| --- | --- | --- |
| `nano-only-recruitment-outputs-2026-07-24/` | The earlier NANO-only milestone HTML/XLSX, written before the combined NANO+NICO generator existed | `projects/nano-nico-recruitment/recruitment_outputs/` |
| `vscode-launch.json` | A stale VS Code Chrome debug config pointing at `localhost:8080`; unrelated to any project here | — |
| `restricted/` | **Git-ignored.** Participant-level output from superseded runs | see below |

## `restricted/` (git-ignored)

| Path | What it is |
| --- | --- |
| `agent-run-outputs-2026-07-24/` | A scratch run of the recruitment ground-truth pipeline, including participant-level workbooks and `.work/` inspection dumps |
| `csbs-invalid-permission-run-2026-07-20/` | The CSBS scoring run made before full-data-set API access was granted; every file is suffixed `.INVALID` |

These contain participant identifiers. The `.gitignore` entry `archive/restricted/` keeps
the whole directory out of version control — do not move files out of it, and do not
commit anything from it.

## Retention

Delete a folder here once you are confident the superseded output is no longer needed for
provenance. The dated recruitment tables under
`projects/nano-nico-recruitment/recruitment_outputs/archive/` are *not* part of this
archive — that directory is the generator's own rolling history and is managed by
`recruitment_reports.py`.
