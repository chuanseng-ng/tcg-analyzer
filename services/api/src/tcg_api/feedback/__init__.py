"""Spec §68's feedback loop — what grade a user actually received.

The eighth schema domain, and the only one whose rows outlive the session that
produced them. `docs/retention.md` carries the justification, which was written
before the table existed: the row holds a prediction, the versions that made it,
the recommendation the user was shown, and a **hash** of a return code that was
displayed once — no photograph, no object key, no session, no analysis, no
address. What survives the seven-day cascade is a prediction with no subject.

**The code is a bearer capability, not an identity.** It is minted at results
time, shown once in one response body, and stored only as a digest. Nothing
joins it to a session or an address, so holding one proves that somebody was
shown it and nothing else — which is what keeps §53's "no login, and do not
permanently tie analyses to personal identity" true of a mechanism that has to
work weeks after a tab was closed.

**Nothing here retrains anything.** §68 puts a validation step between a user's
answer and any future training, and that step is an operator reading a row by
hand (`tcg-review-grade-feedback`). A feedback row is a label with no features:
it names no image, joins to no `physical_copies` row, and enters no dataset
version. Nothing under `tcg_api.datasets` and nothing in `ml/*` may import this
package, and an import-purity test holds it.
"""

from __future__ import annotations
