# Archive

Superseded material, kept for provenance. Nothing here is built, tested or
deployed; nothing in the live board reads from this folder.

| File | Superseded by | Why |
|---|---|---|
| `NOTEBOOK_PROMPT.md` | `esd_scheduler/` + `tests/` | A prompt to *generate* a scoring notebook. The engine it describes now exists as tested code, so the prompt is a historical artefact. |
| `visit-scheduling-architecture` | `ESD-Visit-Scheduling-v3-SPEC.md` | The v1 architecture note. Its pool-relative normalisation is the cold-start bug v3 fixed, so following it would reintroduce a known defect. |
| `ESD-Visitboard-DASHBOARD-PROMPT.md` | `Master-Visitboard-Prompt-v2.md` | The v2 master prompt explicitly consolidates and supersedes it. |
| `build_deck.js` | `deck/build_deck_v3.py` | pptxgenjs builder for the v2 deck. pptxgenjs is not installed; the v3 deck builds with python-pptx. |
| `render_math.py` | `deck/render_math_v3.py` | LaTeX/`sansmath` math renderer. `sansmath.sty` is absent from the local TeX install; v3 renders with matplotlib and needs no TeX. |
| `ESD-Weighted-Visit-Assignment.pptx` / `.pdf` | `ESD-Visit-Scheduling-v3.pptx` | v1 deck. |
| `ESD-Visit-Scheduling-v2.pptx` / `.pdf` | `ESD-Visit-Scheduling-v3.pptx` | v2 deck. Its worked example ranks C, A, B; v3 ranks A, C, B on the same inputs, and the v3 test suite pins the corrected result. |

To restore any of these, `git mv` it back — history is intact either way.
