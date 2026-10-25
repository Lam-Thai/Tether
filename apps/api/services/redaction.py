"""Removes payment-card numbers, US social security numbers and card security codes.

Applied before a message is stored or sent to the model, and again whenever stored text
is shown (older rows), so the same text never leaks through any path. Pure and
deterministic. It is deliberately narrow: a card number needs 13-19 digits that pass the
Luhn check, so phone numbers, prices, times, dates and booking ids stay readable.
"""

import re

CARD_PLACEHOLDER = "[card number removed]"
SSN_PLACEHOLDER = "[SSN removed]"
CODE_PLACEHOLDER = "[security code removed]"

# A run of digit groups joined by spaces, dots or dashes ("4111 1111-1111.1111"), not
# glued to a letter: digits inside an identifier ("a4111...b") are not a card.
_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])\d+(?:[ .-]+\d+)*(?![A-Za-z])")
# UUIDs (booking, service, FAQ ids) are dash-joined digit groups often enough (about 1 in
# 400) to look like a Luhn-valid card, so they are left out of the card search.
_UUID = re.compile(
    r"(?<![0-9A-Za-z-])[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?![0-9A-Za-z-])"
)
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_SECURITY_CODE = re.compile(
    r"\b(?:cvv2?|cvc2?|cid|security[ \t]+code)\b"
    r"[ \t]*(?:is|[:=#-])?[ \t]*(?:is[ \t]+)?\d{3,4}(?!\d)",
    re.IGNORECASE,
)
_MIN_CARD_DIGITS = 13
_MAX_CARD_DIGITS = 19


def redact(text: str) -> str:
    text = _SECURITY_CODE.sub(CODE_PLACEHOLDER, text)
    text = _SSN.sub(SSN_PLACEHOLDER, text)
    out: list[str] = []
    cursor = 0
    for uuid_match in _UUID.finditer(text):
        out.append(_DIGIT_RUN.sub(_redact_run, text[cursor : uuid_match.start()]))
        out.append(uuid_match.group())
        cursor = uuid_match.end()
    out.append(_DIGIT_RUN.sub(_redact_run, text[cursor:]))
    return "".join(out)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redact_run(match: re.Match[str]) -> str:
    run = match.group()
    groups = [(g.group(), g.start(), g.end()) for g in re.finditer(r"\d+", run)]
    out: list[str] = []
    cursor = 0
    index = 0
    while index < len(groups):
        span = _longest_card(groups, index)
        if span is None:
            index += 1
            continue
        last = span
        out.append(run[cursor : groups[index][1]])
        out.append(CARD_PLACEHOLDER)
        cursor = groups[last][2]
        index = last + 1
    out.append(run[cursor:])
    return "".join(out)


def _longest_card(groups: list[tuple[str, int, int]], start: int) -> int | None:
    """Index of the last group of the longest Luhn-valid 13-19 digit span starting at `start`."""
    best: int | None = None
    digits = ""
    for end in range(start, len(groups)):
        digits += groups[end][0]
        if len(digits) > _MAX_CARD_DIGITS:
            break
        if len(digits) >= _MIN_CARD_DIGITS and _luhn_valid(digits):
            best = end
    return best
