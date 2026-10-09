"""English text normalization for the word-chain game.

A message counts as an English answer only when it is ONE word of 3+ letters a-z.
Markdown and quotes around the word, trailing punctuation and accents are ignored:
"**Apple!**" -> "apple", "café" -> "cafe". Anything else (spaces, apostrophes, hyphens,
digits, commands, mentions, links) is not a word and is treated as chat.
"""
import re
import unicodedata


MIN_WORD_LENGTH = 3
_WORD_RE = re.compile(rf"[a-z]{{{MIN_WORD_LENGTH},}}")
# Only markdown and opening quotes/brackets are stripped at the front, so "!apple",
# "/apple", "-apple", "<@123>" and ":emoji:" stay invalid.
_LEADING_STRIP = "*_|~`\"'“‘«([{"
# At the end, any punctuation or symbol is stripped (see _rstrip_punctuation):
# "apple!", "apple.", "**apple**", "||apple||", "apple‼", "apple😀".


def _rstrip_punctuation(text: str) -> str:
    end = len(text)
    while end and unicodedata.category(text[end - 1])[0] in "PS":
        end -= 1
    return text[:end]


def fold(text: str) -> str:
    """Casefold and remove accents: 'Café' -> 'cafe', fullwidth 'ａｐｐｌｅ' -> 'apple'."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(ch)
    )


def parse_en_word(text: str, fold_accents: bool = True) -> str | None:
    """Return the normalized English word, or None when text is not one word of 3+ letters a-z.

    fold_accents=False keeps accents, so a Vietnamese syllable like 'học' is not read as 'hoc'.
    """
    # NFKC first, so fullwidth forms like 'ａｐｐｌｅ！' become 'apple!'.
    t = unicodedata.normalize("NFKC", text).strip()
    if not t or any(ch.isspace() for ch in t):
        return None
    t = _rstrip_punctuation(t.lstrip(_LEADING_STRIP))
    t = fold(t) if fold_accents else t.casefold()
    return t if _WORD_RE.fullmatch(t) else None


def last_letter(text: str) -> str | None:
    """Last letter a-z of text (trailing punctuation and spaces ignored), else None.

    Only used to resume games saved by the old phrase-based English mode.
    """
    t = _rstrip_punctuation(fold(text).rstrip())
    if t and "a" <= t[-1] <= "z":
        return t[-1]
    return None


def mask_en_word(word: str) -> str:
    """'apple' -> 'a____'."""
    return word[0] + "_" * (len(word) - 1)
