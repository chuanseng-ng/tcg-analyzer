"""The canonical card catalog's PostgreSQL side.

`tcg_domain.catalog` says what a card *is*; this package says how the rows are
shaped and how they are loaded. The two are deliberately separate: the domain
package imports nothing but the standard library, so it can be imported by every
ML module without dragging a database driver along, and SQLAlchemy therefore
cannot live there.

Nothing here is a provider. Spec §10's rule — "the canonical card database must
not be tied to a particular external provider" — is enforced by
`card_external_ids` being the only place a provider name may appear, as data.
"""

from __future__ import annotations

from tcg_api.catalog.tables import card_external_ids, cards, metadata, sets

__all__ = ["card_external_ids", "cards", "metadata", "sets"]
