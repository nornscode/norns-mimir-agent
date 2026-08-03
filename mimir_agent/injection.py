"""Prompt-injection screening for content entering persistent memory.

Stored memories are fed back into future prompts, so text that tries to
smuggle instructions to the model must not be persisted. Patterns here are
deliberately conservative: a genuine product fact should never trip them.
"""

import re

_INSTRUCTION_OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+(?:(?:all|any|the|your)\s+)?"
    r"(?:previous|prior|above|earlier|your)\s+"
    r"(?:instructions?|prompts?|directions?|rules?|context)\b",
    re.IGNORECASE,
)

_CONCEALMENT = re.compile(
    r"\bdo\s+not\s+(?:tell|inform|mention\s+(?:this\s+)?to|reveal\s+(?:this\s+)?to)\s+the\s+user\b",
    re.IGNORECASE,
)

# Chat-template control tokens have no place in a stored fact.
_TEMPLATE_TOKENS = (
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "[INST]",
    "[/INST]",
    "<<SYS>>",
)

# Bidi overrides (LRO/RLO) reorder displayed text — flag on first occurrence.
_BIDI_OVERRIDE = re.compile("[\u202d\u202e]")

# Unicode tag block (U+E0000–U+E007F): an invisible ASCII mirror used to hide
# text from human review. No legitimate use in memory content.
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]")

# Invisible characters that can hide text: ZWSP, word joiner + invisible
# operators, BOM. ZWJ/ZWNJ are excluded — they occur legitimately in emoji
# sequences and Arabic/Persian script. A stray one or two survive copy-paste,
# so only a cluster is treated as suspicious.
_HIDDEN_CHARS = re.compile("[\u200b\u2060-\u2064\ufeff]")
_HIDDEN_CHAR_THRESHOLD = 3


def scan(text: str) -> str | None:
    """Return a short reason if text looks like a prompt injection, else None."""
    if _INSTRUCTION_OVERRIDE.search(text):
        return "instruction-override phrase"
    if _CONCEALMENT.search(text):
        return "asks to conceal information from the user"
    lowered = text.lower()
    for token in _TEMPLATE_TOKENS:
        if token.lower() in lowered:
            return f"chat-template token {token}"
    if _TAG_CHARS.search(text):
        return "invisible Unicode tag characters"
    if _BIDI_OVERRIDE.search(text):
        return "bidirectional text override characters"
    if len(_HIDDEN_CHARS.findall(text)) >= _HIDDEN_CHAR_THRESHOLD:
        return "hidden zero-width characters"
    return None
