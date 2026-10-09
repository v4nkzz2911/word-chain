"""Dictionary of 2-syllable Vietnamese words, indexed by first syllable."""
import logging
import random
import sqlite3
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

from vi_text import parse_word


logger = logging.getLogger("hqs-bot")

WORDLIST_URL = "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/master/Viet74K.txt"
# Smaller lists from the same repo that contain words missing from Viet74K
# (Viet39K is left out: all of its 2-syllable words are already in Viet74K).
EXTRA_WORDLIST_URLS = {
    "words_viet22k.txt": "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/master/Viet22K.txt",
    "words_viet11k.txt": "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/master/Viet11K.txt",
}

# Dictionary data by @minhqnd (https://dict.minhqnd.com), CC BY-SA 4.0.
MINHQND_DB_URL = "https://github.com/minhqnd/dictionary/releases/latest/download/dictionary.db"
MINHQND_WORDLIST_FILE = "words_minhqnd.txt"
_MINHQND_QUERY = """
SELECT DISTINCT w.word
FROM words w
JOIN word_definitions wd ON wd.word_id = w.id
JOIN definitions d ON d.id = wd.definition_id
WHERE w.lang_code = 'vi' AND COALESCE(d.definition_lang, 'vi') = 'vi'
"""


def ensure_wordlist(path: Path, url: str = WORDLIST_URL) -> Path:
    """Download the word list if it is not on disk yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading Vietnamese word list from %s", url)
        tmp_path = path.with_suffix(".tmp")
        urllib.request.urlretrieve(url, tmp_path)
        tmp_path.replace(path)
    return path


def ensure_minhqnd_wordlist(path: Path, url: str = MINHQND_DB_URL) -> Path:
    """Build a word list from the minhqnd dictionary database if it is not on disk yet.

    The database is large (~170 MB), so it is downloaded once, the Vietnamese 2-syllable
    words that have a Vietnamese definition are extracted, and the database is deleted.
    """
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    db_path = path.with_name("minhqnd_dictionary.db.tmp")
    tmp_path = path.with_suffix(".tmp")
    try:
        logger.info("Downloading minhqnd dictionary database (~170 MB, first run only) from %s", url)
        urllib.request.urlretrieve(url, db_path)
        conn = sqlite3.connect(db_path)
        try:
            words = sorted({
                parsed[1]
                for (word,) in conn.execute(_MINHQND_QUERY)
                if word and (parsed := parse_word(word))
            })
        finally:
            conn.close()
        tmp_path.write_text("\n".join(words) + "\n", encoding="utf-8")
        tmp_path.replace(path)
        logger.info("Extracted %d words from the minhqnd dictionary", len(words))
    finally:
        db_path.unlink(missing_ok=True)
    return path


class VietnameseDictionary:
    # Use the "common" starter pool only if it has at least this many words.
    _MIN_COMMON_START_POOL = 200

    def __init__(self, is_common_starter: Callable[[str], bool] | None = None) -> None:
        self.words: dict[str, str] = {}  # key -> display text
        self.by_first: dict[str, set[str]] = defaultdict(set)  # first syllable key -> word keys
        self._start_pool: list[str] | None = None
        self._is_common_starter = is_common_starter

    def __len__(self) -> int:
        return len(self.words)

    def __contains__(self, key: str) -> bool:
        return key in self.words

    def add(self, text: str) -> str | None:
        parsed = parse_word(text)
        if not parsed:
            return None
        key, display = parsed
        self.words.setdefault(key, display)
        self.by_first[key.split(" ")[0]].add(key)
        # The starter pool is not rebuilt here: building it is slow, and added words are
        # usually rare ones that would not be good starters anyway.
        return key

    def remove(self, text: str) -> str | None:
        parsed = parse_word(text)
        if not parsed or parsed[0] not in self.words:
            return None
        key = parsed[0]
        del self.words[key]
        self.by_first[key.split(" ")[0]].discard(key)
        if self._start_pool is not None and key in self._start_pool:
            self._start_pool.remove(key)
        return key

    def load_lines(self, lines: Iterable[str]) -> int:
        before = len(self.words)
        for line in lines:
            self.add(line)
        return len(self.words) - before

    def load_file(self, path: Path) -> int:
        with open(path, encoding="utf-8") as file:
            return self.load_lines(file)

    def candidates(self, first_key: str, used: Iterable[str] = ()) -> list[str]:
        """Unused word keys starting with the syllable first_key."""
        used = set(used)
        return [key for key in self.by_first.get(first_key, ()) if key not in used]

    def has_continuation(self, first_key: str, used: Iterable[str]) -> bool:
        used = set(used)
        return any(key not in used for key in self.by_first.get(first_key, ()))

    def build_start_pool(self) -> int:
        """Pick the starter words: they need a few possible continuations and, when possible,
        common syllables. Slow for a full word list: call it from a worker thread after loading.
        """
        playable = [
            key
            for key in self.words
            if len(self.by_first.get(key.split(" ")[1], ())) >= 3
        ]
        if self._is_common_starter is not None:
            common = [key for key in playable if self._is_common_starter(self.words[key])]
            if len(common) >= self._MIN_COMMON_START_POOL:
                playable = common
        self._start_pool = playable
        return len(playable)

    @property
    def start_pool_size(self) -> int:
        return len(self._start_pool) if self._start_pool is not None else 0

    def random_start(self) -> str | None:
        """Random starter word key."""
        if self._start_pool is None:
            self.build_start_pool()
        return random.choice(self._start_pool) if self._start_pool else None
