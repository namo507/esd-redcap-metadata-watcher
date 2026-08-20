#!/usr/bin/env python3
"""Render the v2 visit-assignment equations to transparent, brand-colored PNGs.

Output: math/<name>.png plus math_dims.json (pixel sizes, consumed by build_deck.js).
Requires pdflatex and pdftocairo on PATH.
"""

import glob
import json
import os
import shutil
import subprocess
import sys

BASE = os.environ.get("ESD_BUILD") or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "math")
TMP = os.path.join(BASE, "_tex")
DPI = 420

COLORS = {
    "discovery": "3366FF",
    "jet": "000000",
    "white": "F4F4F6",
    "orange": "F57F00",
    "red": "D74E2D",
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


def render(name, body, color="jet"):
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
        sys.stderr.write("FAILED %s\n%s\n" % (name, proc.stdout[-2500:]))
        return None

    stem = os.path.join(OUT, name)
    subprocess.run(
        ["pdftocairo", "-png", "-transp", "-r", str(DPI), "-singlefile", pdf, stem],
        check=True, capture_output=True,
    )
    return stem + ".png"


# name -> (latex body, brand color).  \mathnormal{\Delta} because sansmath has no upright Delta.
SNIPPETS = {
    # ---- Layer 1: eligibility -------------------------------------------------
    "eligible": (r"$\displaystyle \mathrm{Eligible}(i,v)\;=\;A_{i,v}\;\wedge\;\neg X_{i,v}\;\wedge\;\neg E_{i,f}\;\wedge\;K_{i,v} $", "discovery"),
    "credential": (r"$\displaystyle K_{i,v} = 1 \iff \mathrm{Req}\bigl(\mathrm{type}(v)\bigr) \subseteq \mathrm{Cred}(i) $", "discovery"),

    # ---- Layer 2: the score ---------------------------------------------------
    "score": (r"$\displaystyle S_{i,v}\;=\;w_1 F_{i,f}\;+\;w_2 R_{i,v}\;+\;w_3 L_i\;+\;w_4 D_{i,v}\;+\;w_5 C_{i,f} $", "discovery"),
    "weights": (r"$\displaystyle (w_1, w_2, w_3, w_4, w_5) = (0.30,\; 0.25,\; 0.20,\; 0.15,\; 0.10), \qquad \sum_{k=1}^{5} w_k = 1 $", "jet"),
    "range": (r"$\displaystyle 0 \le F,\,R,\,L,\,D,\,C \le 1 \;\;\Longrightarrow\;\; 0 \le S_{i,v} \le 1 $", "discovery"),

    # ---- the five parts -------------------------------------------------------
    "familiarity": (r"$\displaystyle f_{i,f} = \frac{n_{i,f}}{n_{i,f} + k} $", "jet"),
    "family": (r"$\displaystyle F_{i,f} = \begin{cases} f_{i,f}, & \sigma_f = +1 \\[3pt] 1 - f_{i,f}, & \sigma_f = -1 \end{cases} $", "jet"),
    "recency": (r"$\displaystyle R_{i,v} = \frac{\min(\mathnormal{\Delta} t_i,\, T_{\mathrm{cap}})}{T_{\mathrm{cap}}} $", "jet"),
    "workload": (r"$\displaystyle L_i = 1 - \frac{h_i}{h_{\max}} $", "jet"),
    "travel": (r"$\displaystyle D_{i,v} = 1 - \frac{\tau_{i,v}}{\tau_{\max}} $", "jet"),
    "continuity": (r"$\displaystyle C_{i,f} = \begin{cases} \gamma, & \text{last checkpoint} \\[3pt] 0, & \text{otherwise} \end{cases} $", "jet"),

    # ---- the old family term, shown as the bug it was --------------------------
    "family_old": (r"$\displaystyle F_{i,f} = \sigma_f \cdot \frac{1}{1 + n_{i,f}} $", "red"),

    # ---- Layer 3: ranking -----------------------------------------------------
    "rank": (r"$\displaystyle \mathrm{Rank}_v = \bigl(i_{(1)},\, i_{(2)},\, \ldots,\, i_{(m)}\bigr) \quad\text{with}\quad S_{i_{(1)}} \ge S_{i_{(2)}} \ge \cdots \ge S_{i_{(m)}} $", "discovery"),
    "flag": (r"$\displaystyle S_{i_{(1)}} - S_{i_{(2)}} < \delta \;\;\Longrightarrow\;\; \text{flag for coordinator review} $", "jet"),
    "tie": (r"$\displaystyle \bigl| S_{i,v} - S_{j,v} \bigr| < \varepsilon $", "discovery"),

    # ---- cold start -----------------------------------------------------------
    "coldstart": (r"$\displaystyle N_i < N_{\min} \;\;\Longrightarrow\;\; R_i \leftarrow \operatorname{median}_j R_j, \quad L_i \leftarrow \operatorname{median}_j L_j $", "jet"),

    # ---- beyond one visit at a time -------------------------------------------
    "dp": (r"$\displaystyle V(j) = \max\bigl\{\, V(j-1),\;\; s_j + V(p(j)) \,\bigr\} $", "jet"),
    "assign": (r"$\displaystyle \max_{a} \;\sum_{v \in \mathcal{V}} S_{a(v),\, v} \qquad \text{subject to} \qquad \bigl| a^{-1}(i) \bigr| \le c_i $", "jet"),

    # ---- worked example -------------------------------------------------------
    "ex_a": (r"$\displaystyle S_A = 0.30(0.600) + 0.25(0.056) + 0.20(0.000) + 0.15(0.600) + 0.10(0.050) = 0.289 $", "jet"),
    "ex_b": (r"$\displaystyle S_B = 0.30(0.000) + 0.25(0.222) + 0.20(0.500) + 0.15(0.000) + 0.10(0.000) = 0.156 $", "jet"),
    "ex_c": (r"$\displaystyle S_C = 0.30(0.333) + 0.25(0.444) + 0.20(0.333) + 0.15(0.400) + 0.10(0.000) = 0.338 $", "discovery"),

    # ---- summary --------------------------------------------------------------
    "final": (r"$\displaystyle \mathrm{Offer}(v) = \begin{cases} \text{coordinator reschedules}, & \mathcal{E}_v = \varnothing \\[6pt] \text{top } K \text{ of } \mathrm{Rank}_v, & \text{otherwise} \end{cases} $", "discovery"),

    # ---- notation chips -------------------------------------------------------
    "sym_i": (r"$\displaystyle i \in \mathcal{C} $", "discovery"),
    "sym_v": (r"$\displaystyle v \in \mathcal{V} $", "discovery"),
    "sym_n": (r"$\displaystyle n_{i,f} $", "discovery"),
    "sym_k": (r"$\displaystyle k $", "discovery"),
    "sym_sigma": (r"$\displaystyle \sigma_f \in \{-1, +1\} $", "discovery"),
    "sym_dt": (r"$\displaystyle \mathnormal{\Delta} t_i $", "discovery"),
    "sym_h": (r"$\displaystyle h_i $", "discovery"),
    "sym_tau": (r"$\displaystyle \tau_{i,v} $", "discovery"),
    "sym_E": (r"$\displaystyle \mathcal{E}_v $", "discovery"),
    "sym_S": (r"$\displaystyle S_{i,v} $", "discovery"),
    "sym_delta": (r"$\displaystyle \delta $", "discovery"),
    "sym_gamma": (r"$\displaystyle \gamma $", "discovery"),
}

if __name__ == "__main__":
    for d in (OUT, TMP):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    for name, (body, color) in SNIPPETS.items():
        if render(name, body, color) is None:
            sys.exit("render failed for %s" % name)

    try:
        from PIL import Image
        dims = {}
        for p in sorted(glob.glob(os.path.join(OUT, "*.png"))):
            with Image.open(p) as im:
                dims[os.path.splitext(os.path.basename(p))[0]] = list(im.size)
        with open(os.path.join(BASE, "math_dims.json"), "w") as fh:
            json.dump(dims, fh, indent=1)
        print("wrote math_dims.json (%d entries)" % len(dims))
    except ImportError:
        sys.exit("Pillow required to write math_dims.json")

    shutil.rmtree(TMP, ignore_errors=True)
    print("%d equations rendered to %s" % (len(SNIPPETS), OUT))
