"""Atomic edits tied to exact original draft text."""
from copy import deepcopy
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.brand.retrieval import RetrievalError


class Patch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: Literal['title', 'subject', 'preheader', 'sender_name', 'byline', 'body']
    start: int | None
    end: int | None
    original: str
    replacement: str


class Patches(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_hash: str
    patches: list[Patch]


def apply(draft, patches, expected_hash=None):
    if expected_hash is not None:
        from app.brand.draft_validation import content_hash
        if content_hash(draft) != expected_hash:
            raise RetrievalError('Draft changed since repair was prepared; original retained')
    result = deepcopy(draft)
    grouped = {}
    for patch in patches:
        text = draft.get(patch.field) or ''
        if (patch.start is None or patch.end is None
                or not 0 <= patch.start <= patch.end <= len(text)
                or text[patch.start:patch.end] != patch.original):
            if expected_hash is not None and patch.original and text.count(patch.original) == 1:
                # Recover model offset arithmetic using a unique exact anchor on the same draft hash.
                patch = patch.model_copy(update={'start': text.index(patch.original),
                                                 'end': text.index(patch.original) + len(patch.original)})
            else:
                raise RetrievalError('Repair patch does not match the saved draft; original retained')
        grouped.setdefault(patch.field, []).append(patch)
    for field, edits in grouped.items():
        edits.sort(key=lambda p: (p.start, p.end))
        if any(a.end > b.start or a.start == b.start for a, b in pairwise(edits)):
            raise RetrievalError('Overlapping repair patches; original retained')
        text = draft.get(field) or ''
        for patch in reversed(edits):
            text = text[:patch.start] + patch.replacement + text[patch.end:]
        result[field] = text
    return result
