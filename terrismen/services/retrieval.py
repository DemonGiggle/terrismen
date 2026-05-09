from __future__ import annotations

import re
from collections import OrderedDict


STOP_WORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "when", "then"}


def build_fts_query(text: str, *, max_terms: int = 12) -> str | None:
    terms = [
        term
        for term in re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
        if term not in STOP_WORDS
    ]
    deduped = list(OrderedDict.fromkeys(terms))
    if not deduped:
        return None
    return " OR ".join(deduped[:max_terms])
