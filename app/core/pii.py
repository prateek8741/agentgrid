"""PII detection and masking.

Used in two places:
* logging - every log record is masked before it is emitted;
* LLM safety - user text is redacted before it is sent to third-party LLM APIs
  (see `redact_for_llm`), so raw emails / phone numbers / card numbers never
  leave the platform.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]{1,64})@([\w-]{1,63}(?:\.[\w-]{1,63})+)")
# International / Indian phone numbers: +91 98765 43210, (555) 123-4567, 9876543210 ...
PHONE_RE = re.compile(r"(?<![\w])(\+?\d[\d\s().-]{7,}\d)(?![\w])")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


_DATE_LIKE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}")


def _is_phone(candidate: str) -> bool:
    """Heuristic: 10-15 digits (or 8-15 with a leading +) and not a date."""
    if _DATE_LIKE_RE.search(candidate):
        return False
    n = len(_digits(candidate))
    if candidate.lstrip().startswith("+"):
        return 8 <= n <= 15
    return 10 <= n <= 15


def _join_tokens(tokens: list[str], seps: list[str], start: int, end: int) -> str:
    out = tokens[start]
    for k in range(start, end):
        out += seps[k] + tokens[k + 1]
    return out


def _sub_phones(candidate: str, repl: Callable[[str], str]) -> str:
    """Replace phone numbers inside a PHONE_RE candidate.

    PHONE_RE allows spaces inside a number ("+91 98765 43210"), so adjacent
    numbers ("9876543210 9123456789") or a phone followed by a date are
    matched as one candidate. When the whole candidate is not a phone, split it
    on whitespace and greedily take the longest run of tokens that is one.
    """
    if _is_phone(candidate):
        return repl(candidate)
    parts = re.split(r"(\s+)", candidate)
    tokens, seps = parts[0::2], parts[1::2]
    if len(tokens) > 64:  # pathological input (e.g. a numeric table): mask nothing rather than go quadratic
        return candidate
    pieces: list[str] = []
    i = 0
    while i < len(tokens):
        end = next(
            (j for j in range(len(tokens) - 1, i - 1, -1) if _is_phone(_join_tokens(tokens, seps, i, j))),
            None,
        )
        if end is None:
            pieces.append(tokens[i])
            end = i
        else:
            pieces.append(repl(_join_tokens(tokens, seps, i, end)))
        if end < len(seps):
            pieces.append(seps[end])
        i = end + 1
    return "".join(pieces)


def mask_email(email: str) -> str:
    match = EMAIL_RE.search(email)
    if not match:
        return email
    local, domain = match.group(1), match.group(2)
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    digits = _digits(phone)
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in _digits(number)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def mask_pii(text: str) -> str:
    """Mask PII for logs: keeps a hint (last 4 digits / first letter) for debugging."""
    if not text:
        return text
    text = CARD_RE.sub(lambda m: "[CARD]" if _luhn_ok(m.group(0)) else m.group(0), text)
    text = EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    text = PAN_RE.sub("[PAN]", text)
    text = PHONE_RE.sub(lambda m: _sub_phones(m.group(0), mask_phone), text)
    return text


@dataclass
class Redaction:
    """PII redaction state for one agent turn.

    `protect()` can be called on several texts (user message, history, tool
    results) and always maps the same value to the same placeholder, so the
    model sees a consistent `<EMAIL_1>`. `restore()` puts the real values back
    at the tool boundary and in the final reply to the customer.
    """

    text: str = ""
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    _reverse: dict[str, str] = field(default_factory=dict, repr=False)
    _counters: dict[str, int] = field(default_factory=dict, repr=False)

    def placeholder(self, kind: str, value: str) -> str:
        if value in self._reverse:
            return self._reverse[value]
        self._counters[kind] = self._counters.get(kind, 0) + 1
        token = f"<{kind}_{self._counters[kind]}>"
        self.mapping[token] = value
        self._reverse[value] = token
        return token

    def protect(self, text: str) -> str:
        if not text:
            return text
        for original, token in self._reverse.items():  # values already seen this turn
            text = text.replace(original, token)
        out = CARD_RE.sub(
            lambda m: self.placeholder("CARD", m.group(0)) if _luhn_ok(m.group(0)) else m.group(0), text
        )
        out = AADHAAR_RE.sub(lambda m: self.placeholder("ID", m.group(0)), out)
        out = PAN_RE.sub(lambda m: self.placeholder("ID", m.group(0)), out)
        out = EMAIL_RE.sub(lambda m: self.placeholder("EMAIL", m.group(0)), out)
        return PHONE_RE.sub(
            lambda m: _sub_phones(m.group(0), lambda v: self.placeholder("PHONE", v.strip())), out
        )

    def restore(self, value: str) -> str:
        for placeholder, original in self.mapping.items():
            value = value.replace(placeholder, original)
        return value


def redact_for_llm(text: str) -> Redaction:
    """Replace PII with stable placeholders (`<EMAIL_1>`, `<PHONE_1>`, ...)."""
    redaction = Redaction()
    redaction.text = redaction.protect(text)
    return redaction
