#!/usr/bin/env python3
"""Render LaTeX math snippets to transparent, brand-colored PNGs for the ESD deck."""

import json
import os
import shutil
import subprocess
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tex")

COLORS = {
    "discovery": "3366FF",
    "jet": "000000",
    "white": "F4F4F6",
    "orange": "F57F00",
}

PREAMBLE = r"""
\documentclass[border=3pt]{standalone}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb}
\usepackage[scaled=0.92]{helvet}
\usepackage{sansmath}
\usepackage[dvipsnames]{xcolor}
\renewcommand{\familydefault}{\sfdefault}
\sansmath
\definecolor{brand}{HTML}{%(hex)s}
\begin{document}
\color{brand}
%(body)s
\end{document}
"""


def render(name, body, color="jet", dpi=420):
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    tex_path = os.path.join(TMP, name + ".tex")
    with open(tex_path, "w") as fh:
        fh.write(PREAMBLE % {"hex": COLORS[color], "body": body})

    proc = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
         "-output-directory", TMP, tex_path],
        capture_output=True, text=True,
    )
    pdf = os.path.join(TMP, name + ".pdf")
    if proc.returncode != 0 or not os.path.exists(pdf):
        sys.stderr.write("FAILED %s\n" % name)
        sys.stderr.write(proc.stdout[-2500:])
        return None

    png_stem = os.path.join(OUT, name)
    subprocess.run(
        ["pdftocairo", "-png", "-transp", "-r", str(dpi), "-singlefile", pdf, png_stem],
        check=True, capture_output=True,
    )
    return png_stem + ".png"


# name -> (latex body, brand color)
SNIPPETS = {
    # --- Layer 1 ---
    "eligible": (r"$\displaystyle \mathrm{Eligible}(i,v)\;=\;A_{i,v}\;\wedge\;\neg E_{i,f}\;\wedge\;R^{\mathrm{cert}}_{i,v}\;\wedge\;G_{i,v} $", "discovery"),

    # --- Layer 2 core ---
    "score": (r"$\displaystyle S_{i,v}\;=\;w_1 R_{i,v}\;+\;w_2 F_{i,f}\;+\;w_3 D_{i,v}\;+\;w_4 L_i\;+\;w_5 C_{i,f} $", "discovery"),
    "weights": (r"$\displaystyle \sum_{k=1}^{5} w_k = 1, \qquad w_k \ge 0 $", "jet"),
    "argmax": (r"$\displaystyle \hat{\imath}_v \;=\; \arg\max_{i \in \mathcal{E}_v} \; S_{i,v} $", "discovery"),

    # --- the five parts ---
    "recency": (r"$\displaystyle R_{i,v} = \frac{\min(\Delta t_i,\, T_{\mathrm{cap}})}{T_{\mathrm{cap}}} $", "jet"),
    "family": (r"$\displaystyle F_{i,f} = \sigma_f \cdot \frac{1}{1 + n_{i,f}} $", "jet"),
    "distance": (r"$\displaystyle D_{i,v} = 1 - \frac{d_{i,v}}{d_{\max}} $", "jet"),
    "workload": (r"$\displaystyle L_i = 1 - \frac{v_i^{(\mathrm{period})}}{v_{\max}^{(\mathrm{period})}} $", "jet"),
    "continuity": (r"$\displaystyle C_{i,f} = \begin{cases} \gamma, & \text{same person did the last checkpoint} \\[2pt] 0, & \text{otherwise} \end{cases} $", "jet"),

    # --- worked example ---
    "example_weights": (r"$\displaystyle (w_1,\,w_2,\,w_3,\,w_4,\,w_5) \;=\; (0.30,\; 0.25,\; 0.20,\; 0.15,\; 0.10) $", "jet"),
    "example_a": (r"$\displaystyle S_A = 0.30(0.056) + 0.25(0.500) + 0.20(0.636) + 0.15(0.000) + 0.10(0.050) = \mathbf{0.274} $", "jet"),
    "example_b": (r"$\displaystyle S_B = 0.30(0.222) + 0.25(1.000) + 0.20(0.000) + 0.15(0.500) + 0.10(0.000) = \mathbf{0.392} $", "discovery"),

    # --- Layer 3 ---
    "tie": (r"$\displaystyle \bigl| S_{i,v} - S_{j,v} \bigr| \; < \; \varepsilon $", "discovery"),
    "winner": (r"$\displaystyle \mathrm{Winner} = \begin{cases} \displaystyle\arg\min_{i} \; n_{i,f}, & \sigma_f = -1 \quad \text{(new face preferred)} \\[8pt] \displaystyle\arg\max_{i} \; n_{i,f}, & \sigma_f = +1 \quad \text{(same face preferred)} \end{cases} $", "jet"),

    # --- summary ---
    "final": (r"$\displaystyle \mathrm{Assignment}(v) = \begin{cases} \text{send to coordinator}, & \mathcal{E}_v = \varnothing \\[6pt] \displaystyle\arg\max_{i \in \mathcal{E}_v} S_{i,v}, & \text{otherwise} \end{cases} $", "white"),

    # --- notation chips (single symbols, larger relative size) ---
    "sym_i": (r"$\displaystyle i \in \mathcal{C} $", "discovery"),
    "sym_v": (r"$\displaystyle v \in \mathcal{V} $", "discovery"),
    "sym_dt": (r"$\displaystyle \Delta t_i $", "discovery"),
    "sym_n": (r"$\displaystyle n_{i,f} $", "discovery"),
    "sym_sigma": (r"$\displaystyle \sigma_f \in \{-1, +1\} $", "discovery"),
    "sym_d": (r"$\displaystyle d_{i,v} $", "discovery"),
    "sym_eps": (r"$\displaystyle \varepsilon $", "discovery"),
    "sym_E": (r"$\displaystyle \mathcal{E}_v $", "discovery"),
    "sym_S": (r"$\displaystyle S_{i,v} $", "discovery"),
    "sym_gamma": (r"$\displaystyle \gamma $", "discovery"),
}

if __name__ == "__main__":
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    results = {}
    for key, (body, color) in SNIPPETS.items():
        path = render(key, body, color)
        if path is None:
            sys.exit("render failed for %s" % key)
        results[key] = path
    print(json.dumps({k: os.path.basename(v) for k, v in results.items()}, indent=2))
    print("\n%d formulas rendered to %s" % (len(results), OUT))
