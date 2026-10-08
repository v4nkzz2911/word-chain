"""Vietnamese text normalization for the word-chain game.

"hoà" and "hòa", "thuỷ" and "thủy" are the same syllable written with the tone mark
in a different position. The tone mark is split off each syllable to build a single
key, so players can type either style.
"""
import re
import unicodedata


# Tone marks (combining characters after NFD) -> digit
TONE_MARKS = {
    "́": "1",  # sắc
    "̀": "2",  # huyền
    "̉": "3",  # hỏi
    "̃": "4",  # ngã
    "̣": "5",  # nặng
}

_SYLLABLE_RE = re.compile(r"^[^\W\d_]+$")
_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, Unicode NFC, collapse whitespace."""
    text = unicodedata.normalize("NFC", text).lower().strip()
    return _SPACE_RE.sub(" ", text)


def syllables(text: str) -> list[str]:
    text = normalize_text(text)
    return text.split(" ") if text else []


def is_valid_syllable(syllable: str) -> bool:
    """Letters only (no digits, punctuation or hyphens)."""
    return bool(_SYLLABLE_RE.match(syllable))


def syllable_key(syllable: str) -> str:
    """'hoà' and 'hòa' -> the same key 'hoa2'."""
    decomposed = unicodedata.normalize("NFD", syllable.lower())
    tone = ""
    base = []
    for ch in decomposed:
        if ch in TONE_MARKS:
            tone = TONE_MARKS[ch]
        else:
            base.append(ch)
    return unicodedata.normalize("NFC", "".join(base)) + tone


def word_key(parts: list[str]) -> str:
    return " ".join(syllable_key(part) for part in parts)


def parse_word(text: str) -> tuple[str, str] | None:
    """Return (key, display) if text is exactly 2 valid syllables, else None."""
    parts = syllables(text)
    if len(parts) != 2 or not all(is_valid_syllable(part) for part in parts):
        return None
    return word_key(parts), " ".join(parts)
