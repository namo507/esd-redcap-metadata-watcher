"""Single CLI/notebook entry point for the validated caregiver workbook.

The prior cache-only, archive-merging exporter has been retired. Acquisition,
screening and workbook generation now share the refined pipeline.
"""
from refined_export import main, run_refined_export

if __name__ == "__main__":
    main()
