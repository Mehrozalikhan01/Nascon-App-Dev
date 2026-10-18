"""Synchronous chat normalization and deterministic moderation predicates."""

import re

_PROFANITY = ("idiot_spam", "badword", "asshole", "bitch", "fuck", "shit", "porn", "scam")


def normalize_text(text: str) -> str:
    substitutions = {"@": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s"}
    return "".join(substitutions.get(char.lower(), char.lower()) for char in text if char not in "_.-*")


def _token_pattern(word: str) -> str:
    pieces = []
    for char in word:
        choices = {"a": "a@", "o": "o0", "i": "i1!", "e": "e3", "s": "s$"}.get(char, char)
        pieces.append(f"[{re.escape(choices)}]")
    return r"[_\W*.-]*".join(pieces)


def mask_profanity(text: str) -> tuple[str, bool]:
    matches = []
    for word in _PROFANITY:
        matches.extend((match.start(), match.end()) for match in re.finditer(_token_pattern(word), text, re.IGNORECASE))
    if not matches:
        return text, False
    matches.sort()
    merged = []
    for start, end in matches:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        text = text[:start] + "***" + text[end:]
    return text, True


def is_flood_message(text: str) -> bool:
    return bool(re.search(r"(.)\1{3,}", text, re.IGNORECASE) or re.search(r"[!?]{3,}", text))
