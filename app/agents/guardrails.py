"""Input and output guardrails.

* Prompt-injection heuristics on user input (the system prompt also instructs
  the model to treat user/document text as data).
* Per-tenant blocked terms on input and output.
* Output filter: blocks system-prompt leakage (canary token), unsafe links,
  blocked terms, and caps length.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

INJECTION_PATTERNS = [
    r"\bignore (all |any |the )?(previous|prior|above|earlier|your) (instructions|prompts?|rules)\b",
    r"\bdisregard (all |the |your )?(previous |prior |system )?(instructions|prompt|rules)\b",
    r"\b(reveal|show|print|repeat|tell me) (me )?(your|the) (system )?(prompt|instructions|rules)\b",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\b(developer|god|dan|jailbreak) mode\b",
    r"\bpretend (to be|you are)\b",
    r"\bact as (?!a customer)\w+",
    r"</?(system|assistant|instructions?)>",
    r"\bnew instructions?:",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)
_UNSAFE_LINK_RE = re.compile(r"\]\(\s*(javascript|data|vbscript):[^)]*\)", re.IGNORECASE)

# Random per process: if it ever appears in model output, the system prompt leaked.
CANARY = f"AGC-{secrets.token_hex(6)}"
MAX_REPLY_CHARS = 1500

SAFE_FALLBACK = "Sorry, I can't help with that. Is there anything else I can help you with?"


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def find_blocked_term(text: str, blocked_terms: list[str]) -> str | None:
    lowered = (text or "").lower()
    for term in blocked_terms:
        term = term.strip().lower()
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered):
            return term
    return None


@dataclass
class OutputCheck:
    text: str
    blocked: bool
    reason: str | None = None


def filter_output(text: str | None, blocked_terms: list[str]) -> OutputCheck:
    if not text or not text.strip():
        return OutputCheck(text="", blocked=True, reason="empty")
    if CANARY in text:
        return OutputCheck(text=SAFE_FALLBACK, blocked=True, reason="prompt_leak")
    term = find_blocked_term(text, blocked_terms)
    if term:
        return OutputCheck(text=SAFE_FALLBACK, blocked=True, reason="blocked_term")
    cleaned = _UNSAFE_LINK_RE.sub("](#)", text).strip()
    if len(cleaned) > MAX_REPLY_CHARS:
        cut = cleaned[:MAX_REPLY_CHARS]
        cleaned = cut[: cut.rfind(" ")] + "…" if " " in cut else cut
    return OutputCheck(text=cleaned, blocked=False)
