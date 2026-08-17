"""ESD Lab weighted visit-assignment engine (v3).

Three-layer assignment policy with a full audit trail:

    Layer 0  calendar freshness gate
    Layer 1  hard eligibility filter (boolean AND, never overridable by score)
    Layer 2  weighted score over four non-redundant criteria
    Layer 3  ranking, calibrated review band, tie handling

Everything the engine decides is written to an append-only SQLite audit log so
weights can be validated, drift can be measured, and any past decision can be
replayed under new weights.
"""

__version__ = "3.0.0"

from .config import EngineConfig, WeightVector, load_config  # noqa: F401
