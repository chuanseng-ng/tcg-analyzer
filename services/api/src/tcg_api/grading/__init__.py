"""Grading-company reference data, as this service stores and reads it.

`packages/grading-companies` is where the reference data *is* — spec §22's
adapters, the PSA/TAG/BGS grade scales, and the `GradingRules` record shape.
This package is the database side of it: `tables.py` declares §23's
`grading_rules`, `rules.py` resolves which version was in force on a date, and
`seed.py` writes the published versions from the adapters into the table.

Nothing here defines a grading company. A fourth one is a new adapter in
`tcg_grading_companies` and no change at all in this package — which is §22's
requirement, and the reason `grading_rules.company` carries no CHECK constraint.
"""
