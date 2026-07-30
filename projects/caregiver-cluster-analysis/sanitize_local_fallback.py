"""Blank direct identifiers from tracked CSV fallbacks without changing schema."""

from pathlib import Path
import csv
import re
import tempfile


PROJECT_DIR = Path(__file__).resolve().parent
FILES = [
    PROJECT_DIR / "InfantAutismScreenin-FullDataset_DATA_2026-01-20_0940.csv",
    PROJECT_DIR / "InfantAutismScreenin-FullDataset_DATA_LABELS_2026-01-20_0940.csv",
    PROJECT_DIR / "cleaned_autism_study_data.csv",
]
DIRECT_IDENTIFIER_HEADER = re.compile(
    r"(email|zip|dob|date of birth|occupation|first child born)",
    re.IGNORECASE,
)


for source in FILES:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        raise RuntimeError(f"Empty CSV: {source}")
    header = rows[0]
    identifier_positions = [
        index
        for index, column in enumerate(header)
        if DIRECT_IDENTIFIER_HEADER.search(column)
    ]
    if not identifier_positions:
        continue
    blanked = 0
    for row in rows[1:]:
        for position in identifier_positions:
            if position < len(row) and row[position].strip():
                row[position] = ""
                blanked += 1
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=source.parent,
        prefix=f".{source.name}.",
    ) as temporary:
        writer = csv.writer(temporary, lineterminator="\n")
        writer.writerows(rows)
        temporary_path = Path(temporary.name)
    temporary_path.replace(source)
    print(f"{source.name}: blanked {blanked} direct-identifier cells")
