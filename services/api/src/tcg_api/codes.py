"""Return codes a person can write down — issue #270, spec §68.

A return code is a **bearer capability**: it is minted when a user asks to be
able to report their grade later, shown once in one response body, and stored
only as a digest. Holding one proves that somebody was shown it and nothing
else. It is not an account, it is not joined to a session, and nothing about the
person who holds it is recorded anywhere — which is how spec §53's "no login,
and do not permanently tie analyses to personal identity" stays true of a
mechanism that has to work weeks after a browser tab was closed.

Here rather than in `tcg_api.feedback` for one reason: #148's consent row needs
the same mint for its withdrawal code, it lives under `tcg_api.datasets`, and
nothing under `tcg_api.datasets` may import `tcg_api.feedback` — a purity test
holds that, because §68 puts a validation step between a user's answer and any
future training. This module imports `hashlib` and `secrets` and nothing else,
so it can be imported from anywhere in the service without a cycle.

**The alphabet is Crockford's base32, and the reason is transcription.** The
issue asks for "a form a person can write down", and the failure mode of a
written-down code is not a weak generator, it is a `0` read back as an `O`. So
`I`, `L`, `O` and `U` are absent from what is *rendered*, and :func:`normalise`
folds them onto the digits they get mistaken for on the way back in — the
decoder absorbs the confusion rather than the alphabet shrinking to avoid it.
`secrets.token_urlsafe` was the obvious alternative and loses here: it is
mixed-case base64url, in which `l`, `I`, `O` and `0` all appear and none of them
can be folded, because case is significant and `l` and `I` are distinct symbols.

Twenty symbols is a hundred bits. That is short of a session token's 256 and is
the deliberate trade: this is a string somebody copies onto the back of a
receipt. What it has to survive is guessing at HTTP rates, and the three feedback
routes are rate-limited (ADR 0005) — so the attacker's budget is thirty attempts
a minute against 2^100 possibilities.
"""

from __future__ import annotations

import secrets
from hashlib import sha256
from typing import Final

__all__ = [
    "ALPHABET",
    "GROUPS",
    "GROUP_SIZE",
    "LENGTH",
    "InvalidReturnCode",
    "digest",
    "mint",
    "normalise",
    "render",
]

#: Crockford's base32 — the ten digits and twenty-two letters, without `I`, `L`,
#: `O` and `U`. The first three are the ones a handwritten code loses; `U` is
#: excluded by Crockford so that no accidental word is obscene, which costs
#: nothing.
#:
#: **Upper case is load-bearing**: `grade_feedback.return_code_hash` takes 64
#: *lowercase* hex characters, so a rendered code and a stored digest are
#: disjoint alphabets and the column could not hold a code even by mistake.
ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Four groups of five, `A3KDM-9F2QT-BXWR7-N0HJ5`. Groups because a person
#: reading twenty undifferentiated characters loses their place.
GROUPS: Final = 4
GROUP_SIZE: Final = 5

#: Twenty symbols over a 32-symbol alphabet: a hundred bits.
LENGTH: Final = GROUPS * GROUP_SIZE

#: What a person is likely to have written instead. Folded on the way in, never
#: on the way out — the renderer emits none of these.
_FOLDED: Final = {"O": "0", "I": "1", "L": "1"}

#: Dropped wherever they appear: the rendered hyphens, and whatever whitespace
#: survives a copy and paste.
#: `\u00a0` is spelled out rather than typed: a code pasted back from a web
#: page or a chat window often carries one, and it looks exactly like a space.
_SEPARATORS: Final = frozenset("- \t\r\n" + "\u00a0")


class InvalidReturnCode(ValueError):
    """The string is not a return code at all.

    Distinct from "no feedback is recorded under that code", which is a fact
    about the database and is not this module's to know. A caller answers both
    the same way — one bare 404 — so that a well-formed guess cannot be told
    apart from a malformed one.
    """


def mint() -> str:
    """One unguessable return code, rendered.

    `secrets.choice` per symbol rather than `secrets.token_bytes` plus a base
    conversion: there is no modulo bias to reason about, and twenty draws is not
    a performance question. Never `random` — `sessions.new_session_token`'s rule
    and for its reason.
    """
    return render("".join(secrets.choice(ALPHABET) for _ in range(LENGTH)))


def render(symbols: str) -> str:
    """Group `symbols` for a human to copy: `A3KDM-9F2QT-BXWR7-N0HJ5`."""
    if len(symbols) != LENGTH:
        raise InvalidReturnCode(f"a return code is {LENGTH} symbols, not {len(symbols)}")
    return "-".join(symbols[start : start + GROUP_SIZE] for start in range(0, LENGTH, GROUP_SIZE))


def normalise(code: str) -> str:
    """The canonical symbols of `code`, however it was written down.

    Upper-cased, separators dropped, and `O`/`I`/`L` folded onto `0`/`1`/`1`.
    The folding is why this is not simply `code.upper().replace("-", "")`: a code
    typed back from paper is the case this exists for.

    Raises:
        InvalidReturnCode: If what is left is not `LENGTH` symbols of `ALPHABET`.
    """
    symbols = "".join(
        _FOLDED.get(character, character)
        for character in code.upper()
        if character not in _SEPARATORS
    )
    if len(symbols) != LENGTH or any(symbol not in ALPHABET for symbol in symbols):
        raise InvalidReturnCode("that is not a return code")
    return symbols


def digest(code: str) -> str:
    """The 64 lowercase hex characters `grade_feedback` stores.

    Unsalted and unstretched, deliberately. The pre-image is a hundred bits of
    uniform randomness rather than a password, so there is no dictionary to
    attack; and lookup *by* the digest is the only query the table has, which a
    per-row salt would make impossible.

    Raises:
        InvalidReturnCode: Through :func:`normalise`.
    """
    return sha256(normalise(code).encode()).hexdigest()
