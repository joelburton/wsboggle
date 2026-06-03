"""``GET /api/define`` — word-definition lookup.

Thin route over :mod:`wsboggle.dictionary`. Returns ``definition:
null`` when the word isn't in the bundled dictionary (or the
dictionary file isn't installed). Auth-gated like all other ``/api``
routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from wsboggle import auth, dictionary
from wsboggle.deps import require_user
from wsboggle.shared import DefineResponse


router = APIRouter(prefix="/api")


@router.get("/define", response_model=DefineResponse)
def define_word(
    word: str = Query(..., min_length=1, max_length=64),
    _user: auth.User = Depends(require_user),
) -> DefineResponse:
    """Look up a word's definition.

    Non-alphabetic inputs return ``definition: null`` without
    erroring — the client just shows "no definition," same as for a
    misspelled query.
    """
    cleaned = word.strip()
    if not (cleaned.isascii() and cleaned.isalpha()):
        return DefineResponse(word=cleaned, definition=None)
    return DefineResponse(word=cleaned, definition=dictionary.define(cleaned))
