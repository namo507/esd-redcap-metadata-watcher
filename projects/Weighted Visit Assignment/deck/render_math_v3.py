#!/usr/bin/env python3
"""Render the v3 equations to transparent, brand-coloured PNGs.

Output: deck/math/<name>.png plus deck/math_dims.json (pixel sizes, consumed by
build_deck_v3.py).

The v2 pipeline used pdflatex plus sansmath. This one uses matplotlib's mathtext
instead, so the deck rebuilds on any machine with the project's Python
dependencies and no TeX install at all. Math is set in DejaVu Sans (matplotlib's
bundled sans math font, which has full Greek coverage); Libre Franklin has no
Greek glyphs, so forcing it here would produce a mixed-fallback mess. Every
formula is coloured with a canon brand hex.
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

matplotlib.rcParams["mathtext.fontset"] = "dejavusans"

BASE = os.environ.get("ESD_BUILD") or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "math")
DPI = 400

BRAND = {
    "discovery": "#3366FF",
    "jet": "#000000",
    "white": "#F4F4F6",
    "orange": "#F57F00",
    "red": "#D74E2D",
}

# name -> (latex, colour key, font size in points)
FORMULAS = {
    # Layer 1
    "layer1": (
        r"$F(c,v)=W\wedge A\wedge\neg X\wedge\neg E\wedge K\wedge \mathrm{Cal}\wedge \mathrm{Ramp}$",
        "discovery", 30,
    ),
    # Layer 2 composite
    "composite": (
        r"$S(c,v)=w_\Phi\,\Phi \;+\; w_\Omega\,\Omega \;+\; w_\Psi\,\Psi \;+\; w_P\,P$",
        "discovery", 34,
    ),
    "composite_white": (
        r"$S(c,v)=w_\Phi\,\Phi + w_\Omega\,\Omega + w_\Psi\,\Psi + w_P\,P$",
        "white", 30,
    ),
    # Phi
    "phi_raw": (
        r"$R(c,v)=\left(1-e^{-k_{cf}/\kappa}\right)\cdot e^{-\Delta_{cf}/\tau}$",
        "discovery", 32,
    ),
    "phi_flip": (
        r"$\Phi=\dfrac{1+\sigma_f}{2}\,R \;+\; \dfrac{1-\sigma_f}{2}\,(1-R)$",
        "discovery", 30,
    ),
    "phi_zero": (
        r"$k=0 \;\Longrightarrow\; R=0$",
        "orange", 34,
    ),
    # Psi
    "psi_burden": (
        r"$B(c,v)=\hat{H}_c \;+\; d_v \;+\; \gamma\,\dfrac{T(c,v)}{60}$",
        "discovery", 32,
    ),
    "psi_relief": (
        r"$\Psi = 1-\mathrm{clip}\!\left(\dfrac{B(c,v)}{\mathrm{Cap}_c(t)},\,0,\,1\right)$",
        "discovery", 30,
    ),
    # the finding
    "gamma_implied": (
        r"$\dfrac{\partial S/\partial T_{\mathrm{hr}}}{\partial S/\partial H_{\mathrm{hr}}}"
        r"=\dfrac{0.15\times 60}{R_T}\cdot\dfrac{R_H}{0.20}\approx 12.9$",
        "red", 32,
    ),
    "gamma_eff": (
        r"$w^{\mathrm{eff}}_{\mathrm{travel}}=w_\Psi\cdot"
        r"\dfrac{\gamma R_T/60}{R_H+\gamma R_T/60}$",
        "discovery", 30,
    ),
    # cold start
    "ramp": (
        r"$\mathrm{Cap}_c(t)=\mathrm{Cap}_c^{\mathrm{full}}\cdot"
        r"\min\!\left(1,\;\dfrac{n_c+n_0}{N_{\min}+n_0}\right)$",
        "discovery", 30,
    ),
    "shrinkage": (
        r"$\hat{\theta}_c=\lambda_c\bar{\theta}_c+(1-\lambda_c)\theta_0,"
        r"\qquad \lambda_c=\dfrac{n_c}{n_c+m}$",
        "discovery", 28,
    ),
    # Layer 3
    "epsilon": (
        r"$\varepsilon^{\star}=\min\left\{\varepsilon:\;"
        r"\Pr\left[\mathrm{top\text{-}1\;flips}\mid w\sim\mathrm{Dir}(\alpha)\right]"
        r"\leq 0.10\right\}$",
        "discovery", 26,
    ),
    # optimisation
    "dp": (
        r"$DP[j]=\max\left(DP[j-1],\;\; S(c,j)+DP[p(j)]\right)$",
        "discovery", 32,
    ),
    "dp_trigger": (
        r"$n < 1+\sqrt{D/\bar{d}}\;\;\approx\;\;2.8$",
        "orange", 34,
    ),
    "assignment": (
        r"$\max_{x}\;\sum_{c}\sum_{v}S(c,v)\,x_{cv}\;-\;\Pi\sum_{v}"
        r"\left(1-\sum_{c}x_{cv}\right)$",
        "discovery", 28,
    ),
    "assignment_st": (
        r"$\sum_{c}x_{cv}\leq 1,\qquad \sum_{v}d_v\,x_{cv}\leq \mathrm{Cap}_c,"
        r"\qquad x_{cv}\leq F(c,v)$",
        "jet", 24,
    ),
    "regret": (
        r"$\mathrm{regret}_t=\dfrac{\sum S^{\mathrm{opt}}-\sum S^{\mathrm{greedy}}}"
        r"{\sum S^{\mathrm{opt}}}$",
        "discovery", 30,
    ),
    # validation
    "logit": (
        r"$\Pr(c\mid\mathcal{C}_v)=\dfrac{\exp(\beta^{\top}z_{cv})}"
        r"{\sum_{c'}\exp(\beta^{\top}z_{c'v})}$",
        "discovery", 30,
    ),
    "dematel": (
        r"$T=N(I-N)^{-1},\qquad \mathrm{prominence}=r+c,\qquad \mathrm{relation}=r-c$",
        "discovery", 24,
    ),
}


def render(name: str, latex: str, colour: str, size: int) -> tuple:
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0, 0, latex, fontsize=size, color=BRAND[colour])
    path = os.path.join(OUT, f"{name}.png")
    fig.savefig(path, dpi=DPI, transparent=True, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)

    # Trim any residual transparent margin so placement in the deck is exact.
    img = Image.open(path).convert("RGBA")
    alpha = np.array(img)[:, :, 3]
    ys, xs = np.nonzero(alpha > 8)
    if len(xs):
        img = img.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
        img.save(path)
    return img.size


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    dims = {}
    for name, (latex, colour, size) in FORMULAS.items():
        dims[name] = render(name, latex, colour, size)
        print(f"  {name:18s} {dims[name][0]:>5d} x {dims[name][1]:<5d} {colour}")
    with open(os.path.join(BASE, "math_dims.json"), "w", encoding="utf-8") as fh:
        json.dump(dims, fh, indent=2, sort_keys=True)
    print(f"\n{len(dims)} formulas -> {OUT}")


if __name__ == "__main__":
    main()
