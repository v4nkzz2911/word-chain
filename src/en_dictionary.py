"""English word list (ENABLE, public domain) indexed by first letter."""
import logging
import random
import shutil
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from wordfreq import top_n_list, zipf_frequency

from en_text import parse_en_word


logger = logging.getLogger("hqs-bot")

# ENABLE (Enhanced North American Benchmark Lexicon), public domain, ~172,823 words.
EN_WORDLIST_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"
EN_WORDLIST_FILE = "words_en_enable.txt"  # matched by .gitignore data/words*.txt
EN_EXTRA_WORDS_FILE = "extra_words_en.txt"  # optional, one word per line
# A complete ENABLE list has ~172,000 words of 3+ letters; fewer means a broken download.
MIN_EXPECTED_WORDS = 100_000
DOWNLOAD_TIMEOUT_SECONDS = 60

# ---------- words the bot never shows on its own ----------
# Players may still play these if they are in the word list; the bot just never picks them
# as starter words, hints or "could have continued with" answers.
# Any word starting with one of these stems (fucking, shitty, cunts, niggers, ...).
_OFFENSIVE_PREFIXES = (
    "fuck", "motherfuck", "shit", "cunt", "nigg", "nigra", "negro", "fagg", "whore", "slut",
    "twat", "wank", "jizz", "porn", "dildo", "bastard", "asshole", "arsehole", "bollock",
    "bugger", "masturbat", "fellat", "cunnilingu", "sodomi", "sodomy", "pedophil", "paedophil",
    "pederast", "incest", "molest", "orgasm", "erotic", "vagina", "clitor", "penis", "penile",
    "testicl", "scrot", "nazi", "hitler", "honkie", "honky", "redskin", "raghead", "towelhead",
    "wetback", "spaz", "tranny", "trannie", "pussy", "pussies", "blowjob", "handjob", "piss",
    "boner", "horny", "sexy", "skank", "bullshit", "horseshit", "jackass", "douche", "spastic",
    "darkie", "darky", "kaffir", "kafir", "fagot",
)
# Exact words plus their -s/-es/-ed/-ing forms, for stems that also start ordinary words
# (dick -> dictionary, cock -> cocktail, anal -> analyze, rape -> rapeseed, tit -> title).
_OFFENSIVE_WORDS = (
    "dick", "dickhead", "cock", "cocky", "cocksucker", "anal", "anus", "rape", "rapist", "dyke",
    "bitch", "bitchy", "retard", "boob", "booby", "boobies", "tit", "titty", "titties", "cum",
    "cumming", "semen", "sperm", "spick", "kike", "chink", "gook", "coon", "wop", "dago", "gyp", "gypped", "gypping", "homo", "lesbo", "queer", "crap", "crappy", "ass",
    "arse", "slag", "hooker", "harlot", "pimp", "nude", "nudie", "nudity", "orgy", "orgies",
    "sex", "sexual", "lewd", "smut", "smutty", "kinky", "fetish", "bimbo", "hussy", "suicide",
    "fag", "damn", "goddamn", "dammit", "prick", "turd",
)


def _word_forms(word: str) -> set[str]:
    forms = {word, word + "s", word + "es", word + "ed", word + "ing"}
    if word.endswith("e"):
        forms |= {word + "d", word[:-1] + "ing"}
    return forms


# Exact words only, no generated forms ("spic" + "ed" would be "spiced").
_OFFENSIVE_EXACT_ONLY = (
    "spic", "spics", "jap", "japs", "jew", "jews", "jewed", "jewing", "yid", "yids", "wog", "wogs", "squaw", "squaws",
    "hell", "hells",
)

OFFENSIVE_WORDS = frozenset(
    {form for word in _OFFENSIVE_WORDS for form in _word_forms(word)} | set(_OFFENSIVE_EXACT_ONLY)
)


def is_offensive(word: str) -> bool:
    return word in OFFENSIVE_WORDS or word.startswith(_OFFENSIVE_PREFIXES)


# Common words that are mostly read as names, places or brands: never used as starters.
EN_STARTER_EXCLUDE = frozenset({
    # names
    "carl", "lewis", "chris", "christian", "johnny", "billy", "bobby", "jimmy", "tommy", "danny",
    "kelly", "betty", "jerry", "harry", "terry", "larry", "gary", "henry", "rick", "nick", "mike",
    "pete", "victor", "eugene", "willie", "allen", "martin", "russell", "warren", "graham",
    "gordon", "douglas", "howard", "norman", "marshall", "stewart", "stuart", "bruce", "dean",
    "pierre", "louis", "alan", "benjamin", "jesse", "joseph", "logan", "ralph", "roger", "anna",
    "beth", "bonnie", "carmen", "carol", "donna", "erica", "gloria", "jane", "jean", "jill",
    "laura", "maria", "regina", "ruth", "sheila", "stella", "vera", "veronica", "victoria",
    "alma", "romeo",
    # places
    "texas", "china", "alaska", "brazil", "chile", "florence", "holland", "wales", "israel",
    "india", "jordan", "japan", "paris", "london", "boston", "sydney",
    # people, brands, religion
    "trump", "obama", "biden", "clinton", "kennedy", "reagan", "lincoln", "jefferson", "madison",
    "hamilton", "franklin", "washington", "darwin", "einstein", "marx", "moses", "noah",
    "abraham", "jesus", "christ", "christmas", "easter", "allah", "satan", "buddha", "muslim",
    "jewish", "islam", "koran", "google", "yahoo", "kodak", "xerox", "jello", "frisbee", "lego",
    "ford", "honda", "tesla", "amazon", "ajax",
})


def ensure_en_wordlist(path: Path, url: str = EN_WORDLIST_URL) -> Path:
    """Download the English word list if it is not on disk yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading English word list (ENABLE) from %s", url)
        tmp_path = path.with_suffix(".tmp")
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                head = response.read(4096)
                if head.lstrip().startswith(b"<"):
                    raise OSError(f"Downloaded English word list from {url} looks like HTML, not a word list")
                with open(tmp_path, "wb") as file:
                    file.write(head)
                    shutil.copyfileobj(response, file)
            tmp_path.replace(path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return path


class EnglishDictionary:
    STARTER_MIN_LEN = 4
    STARTER_MAX_LEN = 8
    STARTER_MIN_ZIPF = 3.5
    # Words at least this common are preferred for hints and example answers.
    COMMON_MIN_ZIPF = 3.0
    # Starters ending in these letters leave few or awkward continuations.
    STARTER_BAD_ENDINGS = frozenset("jqxyz")
    _FREQ_SCAN_SIZE = 80_000
    # Use the "common" starter pool only if it has at least this many words.
    _MIN_COMMON_START_POOL = 200

    def __init__(self, starter_blacklist: Iterable[str] = ()) -> None:
        self.words: set[str] = set()
        self.by_first: dict[str, set[str]] = defaultdict(set)  # first letter -> words
        self._common_by_first: dict[str, set[str]] = defaultdict(set)  # subset of words
        self._start_pool: list[str] | None = None
        self._blacklist = frozenset(word.casefold() for word in starter_blacklist)

    @property
    def starter_blacklist(self) -> frozenset[str]:
        return self._blacklist

    def __len__(self) -> int:
        return len(self.words)

    def __contains__(self, word: str) -> bool:
        return word in self.words

    def add(self, text: str) -> str | None:
        word = parse_en_word(text)
        if word is None:
            return None
        self.words.add(word)
        self.by_first[word[0]].add(word)
        # The starter pool is not rebuilt here (slow); added words are usually rare ones.
        if (
            self._start_pool is not None
            and not is_offensive(word)
            and zipf_frequency(word, "en") >= self.COMMON_MIN_ZIPF
        ):
            self._common_by_first[word[0]].add(word)
        return word

    def remove(self, text: str) -> str | None:
        word = parse_en_word(text)
        if word is None or word not in self.words:
            return None
        self.words.discard(word)
        self.by_first[word[0]].discard(word)
        self._common_by_first[word[0]].discard(word)
        if self._start_pool is not None and word in self._start_pool:
            self._start_pool.remove(word)
        return word

    def load_lines(self, lines: Iterable[str]) -> int:
        before = len(self.words)
        for line in lines:
            self.add(line)  # 1-2 letter words are dropped by the minimum length
        return len(self.words) - before

    def load_file(self, path: Path) -> int:
        with open(path, encoding="utf-8") as file:
            return self.load_lines(file)

    def candidates(self, letter: str, used: Iterable[str] = ()) -> list[str]:
        """Unused words starting with letter."""
        used = set(used)
        return [word for word in self.by_first.get(letter, ()) if word not in used]

    def count_continuations(self, letter: str, used: Iterable[str] = ()) -> int:
        bucket = self.by_first.get(letter, set())
        return len(bucket) - sum(1 for word in set(used) if word in bucket)

    def has_continuation(self, letter: str, used: Iterable[str]) -> bool:
        return self.count_continuations(letter, used) > 0

    def example(self, letter: str, used: Iterable[str] = ()) -> str | None:
        """A random unused word starting with letter, preferring common words.

        Never returns an offensive word (None when only offensive words are left).
        """
        used = set(used)
        common = [
            word for word in self._common_by_first.get(letter, ())
            if word not in used and not is_offensive(word)
        ]
        if common:
            return random.choice(common)
        options = [word for word in self.candidates(letter, used) if not is_offensive(word)]
        return random.choice(options) if options else None

    def _is_starter_shape(self, word: str) -> bool:
        return (
            self.STARTER_MIN_LEN <= len(word) <= self.STARTER_MAX_LEN
            and word[-1] not in self.STARTER_BAD_ENDINGS
            and word not in self._blacklist
            and word not in EN_STARTER_EXCLUDE
            and not is_offensive(word)
            and bool(self.by_first.get(word[-1]))
        )

    def build_indexes(self) -> int:
        """Build the common-word index and the starter pool.

        Slow for a full word list: call it from a worker thread after loading.
        """
        self._common_by_first.clear()
        pool: list[str] = []
        for word in top_n_list("en", self._FREQ_SCAN_SIZE):
            if word not in self.words or is_offensive(word):
                continue
            zipf = zipf_frequency(word, "en")
            if zipf < self.COMMON_MIN_ZIPF:
                continue
            self._common_by_first[word[0]].add(word)
            if zipf >= self.STARTER_MIN_ZIPF and self._is_starter_shape(word):
                pool.append(word)
        if len(pool) < self._MIN_COMMON_START_POOL:
            # Tiny word list (tests, broken frequency data): use every word of the right shape.
            pool = sorted(word for word in self.words if self._is_starter_shape(word))
        self._start_pool = pool
        return len(pool)

    @property
    def start_pool_size(self) -> int:
        return len(self._start_pool) if self._start_pool is not None else 0

    def random_start(self) -> str | None:
        """Random starter word."""
        if self._start_pool is None:
            self.build_indexes()
        return random.choice(self._start_pool) if self._start_pool else None
