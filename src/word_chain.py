import json
import logging
import random
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wordfreq import zipf_frequency

from en_dictionary import (
    EN_EXTRA_WORDS_FILE,
    EN_WORDLIST_FILE,
    MIN_EXPECTED_WORDS,
    EnglishDictionary,
    ensure_en_wordlist,
    is_offensive,
)
from en_text import last_letter, mask_en_word, parse_en_word
from storage import GameStore
from vi_dictionary import (
    EXTRA_WORDLIST_URLS,
    MINHQND_WORDLIST_FILE,
    VietnameseDictionary,
    ensure_minhqnd_wordlist,
    ensure_wordlist,
)
from vi_text import parse_word, syllable_key


logger = logging.getLogger("hqs-bot")


# Daily limits (e.g. hints) reset at midnight Vietnam time. Vietnam has no daylight saving time.
VIETNAM_TZ = timezone(timedelta(hours=7))

LANGUAGE_LABELS = {
    "en": "English",
    "vi": "Vietnamese",
}

LANGUAGE_LABELS_VI = {
    "en": "Tiếng Anh",
    "vi": "Tiếng Việt",
}

STARTER_BLACKLIST = {
    "en": {
        "john",
        "mary",
        "james",
        "michael",
        "david",
        "robert",
        "jennifer",
        "sarah",
        "paul",
        "peter",
        "london",
        "paris",
        "berlin",
        "rome",
        "madrid",
        "tokyo",
        "seoul",
        "beijing",
        "sydney",
        "dubai",
        "moscow",
        "new york",
    },
    "vi": {
        "hà nội",
        "hải phòng",
        "đà nẵng",
        "sài gòn",
        "hồ chí minh",
    },
}


class PhraseStatus:
    OK = "ok"                    # accepted
    WIN = "win"                  # accepted and nothing can follow -> player wins
    INVALID = "invalid"          # not a usable phrase
    COOLDOWN = "cooldown"        # player is still on cooldown
    WRONG_START = "wrong_start"  # does not start with the expected word
    NOT_IN_DICT = "not_in_dict"  # unknown word(s)
    USED = "used"                # already used in this game
    BANNED = "banned"            # offensive word: refused with a warning (English games only)


class SkipStatus:
    ERROR = "error"      # no game here, or already voted
    VOTED = "voted"      # vote counted, more votes needed
    SKIPPED = "skipped"  # enough votes: word skipped (a new round starts if possible)


@dataclass
class PhraseResult:
    status: str
    message: str

    @property
    def accepted(self) -> bool:
        return self.status in (PhraseStatus.OK, PhraseStatus.WIN)


@dataclass
class WordChainState:
    current_phrase: str
    expected_start_word: str
    language: str
    # Comparison key of the expected start: last letter a-z (en) or syllable key (vi).
    expected_start_key: str = ""
    # Normalized phrase / word key -> name of the player who used it (None = bot starter).
    used: dict[str, str | None] = field(default_factory=dict)
    last_player_id: int | None = None
    turns: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WordChainState":
        return cls(**json.loads(raw))


class WordChainGameManager:
    COOLDOWN_SECONDS = 5.0
    HINTS_PER_DAY = 5
    SKIP_VOTES_NEEDED = 2
    # Each syllable of a Vietnamese starter word must be at least this common.
    _VI_STARTER_SYLLABLE_MIN_ZIPF = 3.5

    def __init__(
        self,
        default_language: str = "en",
        ui_language: str | None = None,
        store: GameStore | None = None,
        vi_dictionary: VietnameseDictionary | None = None,
        en_dictionary: EnglishDictionary | None = None,
    ) -> None:
        self._active_channel_id: int | None = None
        self._active_game: WordChainState | None = None
        self._last_answer_at: dict[tuple[int, int], float] = {}
        normalized_default = self.normalize_language(default_language)
        self._default_language = normalized_default or "en"
        normalized_ui = self.normalize_language(ui_language) if ui_language is not None else None
        self._ui_language = normalized_ui or self._default_language
        self._hints_used: dict[tuple[int, str], int] = {}  # used only without a store
        self._skip_votes: set[int] = set()  # players voting to skip the current word
        self._store = store
        self._vi_dictionary = (
            vi_dictionary
            if vi_dictionary is not None
            else VietnameseDictionary(is_common_starter=self._is_common_vi_starter)
        )
        self._en_dictionary = (
            en_dictionary
            if en_dictionary is not None
            else EnglishDictionary(starter_blacklist=STARTER_BLACKLIST["en"])
        )

        if self._store is not None:
            saved = self._store.load_game()
            if saved is not None:
                self._active_channel_id, state_json = saved
                self._active_game = WordChainState.from_json(state_json)
                if self._active_game.language == "vi":
                    # Keys may come from an older normalization: rebuild the one that matters.
                    self._active_game.expected_start_key = syllable_key(self._active_game.expected_start_word)
                elif self._active_game.language == "en":
                    self._migrate_english_state()

    @property
    def is_vietnamese_ui(self) -> bool:
        return self._ui_language == "vi"

    @property
    def vi_dictionary(self) -> VietnameseDictionary:
        return self._vi_dictionary

    @property
    def en_dictionary(self) -> EnglishDictionary:
        return self._en_dictionary

    def _migrate_english_state(self) -> None:
        """Games saved by the old phrase-based English mode continue from the last letter."""
        game = self._active_game
        key = game.expected_start_key
        if len(key) == 1 and "a" <= key <= "z":
            return
        letter = last_letter(game.expected_start_word) or last_letter(game.current_phrase)
        if letter is None:
            logger.warning("Dropping a saved English game that cannot be resumed: %r", game.current_phrase)
            self._active_channel_id = None
            self._active_game = None
            self._save()
            return
        game.expected_start_word = letter
        game.expected_start_key = letter
        self._save()

    def _tr(self, en: str, vi: str) -> str:
        return vi if self.is_vietnamese_ui else en

    def _label_for_language(self, language: str) -> str:
        if self.is_vietnamese_ui:
            return LANGUAGE_LABELS_VI.get(language, language)
        return LANGUAGE_LABELS.get(language, language)

    # ---------- dictionary ----------
    def load_vietnamese_word_files(self, data_dir: Path) -> int:
        """Load the Viet74K word list (downloaded on first run) and data/extra_words.txt.

        Blocking file/network I/O: run it in a worker thread.
        """
        self._vi_dictionary.load_file(ensure_wordlist(data_dir / "words.txt"))
        for file_name, url in EXTRA_WORDLIST_URLS.items():
            try:
                self._vi_dictionary.load_file(ensure_wordlist(data_dir / file_name, url))
            except OSError:
                logger.warning("Could not load optional word list %s", file_name, exc_info=True)
        try:
            self._vi_dictionary.load_file(ensure_minhqnd_wordlist(data_dir / MINHQND_WORDLIST_FILE))
        except (OSError, sqlite3.Error):
            logger.warning("Could not load the minhqnd word list", exc_info=True)
        extra = data_dir / "extra_words.txt"  # optional: one word per line
        if extra.exists():
            self._vi_dictionary.load_file(extra)
        self._vi_dictionary.build_start_pool()
        return len(self._vi_dictionary)

    @classmethod
    def _is_common_vi_starter(cls, display: str) -> bool:
        return all(
            zipf_frequency(syllable, "vi") >= cls._VI_STARTER_SYLLABLE_MIN_ZIPF
            for syllable in display.split(" ")
        )

    def load_english_word_files(self, data_dir: Path) -> int:
        """Load the ENABLE word list (downloaded on first run) and data/extra_words_en.txt.

        Blocking file/network I/O: run it in a worker thread. The words are loaded into a new
        dictionary that replaces the current one only when everything succeeded, so a failed
        load leaves English games unavailable instead of half-working.
        """
        dictionary = EnglishDictionary(starter_blacklist=self._en_dictionary.starter_blacklist)
        path = ensure_en_wordlist(data_dir / EN_WORDLIST_FILE)
        count = dictionary.load_file(path)
        if count < MIN_EXPECTED_WORDS:
            # Move the broken file away so the next start downloads it again.
            # "words_en_enable.bad.txt" still matches the data/words*.txt .gitignore rule.
            bad_path = path.with_name(path.stem + ".bad" + path.suffix)
            path.replace(bad_path)
            logger.warning(
                "English word list %s looks incomplete (%d words); moved it to %s, "
                "it will be downloaded again on the next start",
                path, count, bad_path,
            )
            raise ValueError(
                f"English word list {path} looks incomplete ({count} words); moved to {bad_path.name}"
            )
        extra = data_dir / EN_EXTRA_WORDS_FILE  # optional: one word per line
        if extra.exists():
            dictionary.load_file(extra)
        dictionary.build_indexes()
        self._en_dictionary = dictionary
        return len(dictionary)

    def apply_custom_words(self, language: str | None = None) -> None:
        """Apply admin dictionary edits saved in the database.

        language limits the edits to one dictionary ("en" or "vi"); None applies both.
        Rows are routed by shape: one a-z token is English, anything else is Vietnamese.
        """
        if self._store is None:
            return
        for word, added in self._store.custom_words():
            if parse_en_word(word, fold_accents=False) is not None:  # one a-z token -> English
                if language in (None, "en"):
                    if added:
                        self._en_dictionary.add(word)
                    else:
                        self._en_dictionary.remove(word)
            elif language in (None, "vi"):
                if added:
                    self._vi_dictionary.add(word)
                else:
                    self._vi_dictionary.remove(word)

    # ---------- game state ----------
    @staticmethod
    def normalize_language(language: str | None) -> str | None:
        if language is None:
            return None

        value = language.strip().lower()
        if value in {"en", "english"}:
            return "en"
        if value in {"vi", "vietnamese", "tieng viet", "tiếng việt"}:
            return "vi"
        return None

    def _new_state(self, language: str) -> WordChainState | None:
        if language == "vi":
            blacklist = STARTER_BLACKLIST["vi"]
            for _ in range(20):
                key = self._vi_dictionary.random_start()
                if key is None:
                    return None
                display = self._vi_dictionary.words[key]
                if display not in blacklist:
                    break
            last_syllable = display.split(" ")[-1]
            return WordChainState(
                current_phrase=display,
                expected_start_word=last_syllable,
                language=language,
                expected_start_key=syllable_key(last_syllable),
                used={key: None},
            )

        starter = self._en_dictionary.random_start()
        if starter is None:
            return None
        letter = starter[-1]
        return WordChainState(
            current_phrase=starter,
            expected_start_word=letter,
            language=language,
            expected_start_key=letter,
            used={starter: None},
        )

    def _save(self) -> None:
        if self._store is None:
            return
        if self._active_game is None or self._active_channel_id is None:
            self._store.delete_game()
        else:
            self._store.save_game(self._active_channel_id, self._active_game.to_json())

    def _bump(self, user_id: int, stat: str, language: str) -> None:
        """Count a stat for the game language: English and Vietnamese stats are separate."""
        if self._store is not None:
            self._store.bump(user_id, stat, language)

    def _round_intro(self, game: WordChainState, title: str) -> str:
        language_label = self._label_for_language(game.language)
        if game.language == "vi":
            return self._tr(
                f"{title}\n"
                f"> **Language:** `{language_label}`\n"
                f"> **Starter word:** `{game.current_phrase}`\n"
                f"> **Next word must start with:** `{game.expected_start_word}`\n"
                "> **Rule:** exactly 2 syllables\n"
                f"> **Cooldown per user:** `{int(self.COOLDOWN_SECONDS)}s`",
                f"{title}\n"
                f"> **Ngôn ngữ:** `{language_label}`\n"
                f"> **Từ bắt đầu:** `{game.current_phrase}`\n"
                f"> **Từ tiếp theo phải bắt đầu bằng:** `{game.expected_start_word}`\n"
                "> **Luật:** đúng 2 âm tiết\n"
                f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
            )
        return self._tr(
            f"{title}\n"
            f"> **Language:** `{language_label}`\n"
            f"> **Starter word:** `{game.current_phrase}`\n"
            f"> **Next word must start with the letter:** `{game.expected_start_word}`\n"
            "> **Rule:** one English word, 3+ letters (a–z)\n"
            f"> **Cooldown per user:** `{int(self.COOLDOWN_SECONDS)}s`",
            f"{title}\n"
            f"> **Ngôn ngữ:** `{language_label}`\n"
            f"> **Từ bắt đầu:** `{game.current_phrase}`\n"
            f"> **Từ tiếp theo phải bắt đầu bằng chữ:** `{game.expected_start_word}`\n"
            "> **Luật:** một từ tiếng Anh, từ 3 chữ cái trở lên (a–z)\n"
            f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
        )

    def _vi_example_answer(self, game: WordChainState) -> str | None:
        options = self._vi_dictionary.candidates(game.expected_start_key, game.used)
        return self._vi_dictionary.words[random.choice(options)] if options else None

    def _en_example_answer(self, game: WordChainState) -> str | None:
        return self._en_dictionary.example(game.expected_start_key, game.used)

    def _en_unavailable_message(self) -> str:
        return self._tr(
            "❌ **The English dictionary is not available**\n"
            "> The word list could not be loaded. Check the bot logs and restart the bot.",
            "❌ **Từ điển tiếng Anh chưa sẵn sàng**\n"
            "> Không tải được danh sách từ. Hãy kiểm tra log của bot và khởi động lại bot.",
        )

    def _unsupported_language_message(self) -> str:
        return self._tr(
            "❌ **Unsupported language**\n> Use `en` (English) or `vi` (Vietnamese).",
            "❌ **Ngôn ngữ không hỗ trợ**\n> Hãy dùng `en` (Tiếng Anh) hoặc `vi` (Tiếng Việt).",
        )

    def start_game(self, channel_id: int, language: str | None = None) -> tuple[bool, str]:
        if self._active_game is not None:
            return False, self._tr(
                "❌ **A word-chain game is already active**\n> Stop the current game before starting a new one.",
                "❌ **Đã có trò chơi nối từ đang hoạt động**\n> Hãy dừng trò chơi hiện tại trước khi bắt đầu trò mới.",
            )

        normalized_language = self.normalize_language(language)
        if language is not None and normalized_language is None:
            return False, self._unsupported_language_message()
        selected_language = normalized_language or self._default_language
        if selected_language == "en" and not len(self._en_dictionary):
            return False, self._en_unavailable_message()

        game = self._new_state(selected_language)
        if game is None:
            return False, self._tr(
                "❌ **No suitable starter words available**\n> No valid starter word was found for the selected language.",
                "❌ **Không có từ bắt đầu phù hợp**\n> Không tìm thấy từ hợp lệ cho ngôn ngữ đã chọn.",
            )

        self._active_channel_id = channel_id
        self._active_game = game
        self._last_answer_at.clear()
        self._skip_votes.clear()
        self._save()
        return True, self._round_intro(
            game, self._tr("✅ **Word-chain game started**", "✅ **Trò chơi nối từ đã bắt đầu**")
        )

    def stop_game(self, channel_id: int) -> tuple[bool, str]:
        game = self._active_game
        if game is None:
            return False, self._tr(
                "❌ **No active word-chain game**\n> There is no running game in this channel.",
                "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào.",
            )

        if self._active_channel_id != channel_id:
            return False, self._tr(
                "❌ **The active game is in another channel**\n> Stop it from the channel where it started.",
                "❌ **Trò chơi đang ở kênh khác**\n> Hãy dừng trò chơi tại kênh đã bắt đầu nó.",
            )

        summary = self._tr(
            f"> Lasted **{game.turns}** turn(s).",
            f"> Kéo dài **{game.turns}** lượt nối.",
        )
        if game.language == "vi":
            hint = self._vi_example_answer(game)
            if hint:
                summary += self._tr(
                    f"\n> Could have continued with: `{hint}`",
                    f"\n> Có thể nối bằng: `{hint}`",
                )
        elif game.language == "en":
            hint = self._en_example_answer(game)
            if hint:
                summary += self._tr(
                    f"\n> Could have continued with: `{hint}`",
                    f"\n> Có thể nối bằng: `{hint}`",
                )

        self._active_channel_id = None
        self._active_game = None
        self._last_answer_at.clear()
        self._skip_votes.clear()
        self._save()
        return True, self._tr("✅ **Word-chain game stopped**\n", "✅ **Đã dừng trò chơi nối từ**\n") + summary

    def has_game(self, channel_id: int) -> bool:
        return self._active_game is not None and self._active_channel_id == channel_id

    def game_status(self, channel_id: int) -> tuple[bool, str]:
        game = self._active_game
        if game is None:
            return False, self._tr(
                "❌ **No active word-chain game in this channel**\n> There is no running game in this channel.",
                "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào.",
            )

        if self._active_channel_id != channel_id:
            return False, self._tr(
                "❌ **The active word-chain game is running in another channel**",
                "❌ **Trò chơi nối từ đang chạy ở kênh khác**",
            )

        language_label = self._label_for_language(game.language)
        if game.language == "en":
            return True, self._tr(
                "📋 **Word-chain status**\n"
                f"> **Language:** `{language_label}`\n"
                f"> **Current word:** `{game.current_phrase}`\n"
                f"> **Next word must start with the letter:** `{game.expected_start_word}`\n"
                f"> **Turns:** `{game.turns}`\n"
                f"> **Per-user cooldown:** `{int(self.COOLDOWN_SECONDS)}s`",
                "📋 **Trạng thái nối từ**\n"
                f"> **Ngôn ngữ:** `{language_label}`\n"
                f"> **Từ hiện tại:** `{game.current_phrase}`\n"
                f"> **Từ tiếp theo phải bắt đầu bằng chữ:** `{game.expected_start_word}`\n"
                f"> **Số lượt nối:** `{game.turns}`\n"
                f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
            )
        return True, self._tr(
            "📋 **Word-chain status**\n"
            f"> **Language:** `{language_label}`\n"
            f"> **Current phrase:** `{game.current_phrase}`\n"
            f"> **Expected start word:** `{game.expected_start_word}`\n"
            f"> **Turns:** `{game.turns}`\n"
            f"> **Per-user cooldown:** `{int(self.COOLDOWN_SECONDS)}s`",
            "📋 **Trạng thái nối từ**\n"
            f"> **Ngôn ngữ:** `{language_label}`\n"
            f"> **Cụm từ hiện tại:** `{game.current_phrase}`\n"
            f"> **Từ bắt đầu yêu cầu:** `{game.expected_start_word}`\n"
            f"> **Số lượt nối:** `{game.turns}`\n"
            f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
        )

    # ---------- hints ----------
    @staticmethod
    def _mask_hint(display: str) -> str:
        """'lực sĩ' -> 'lực s_': first syllable in full, only the first letter of the second."""
        first, second = display.split(" ")
        return f"{first} {second[0]}{'_' * (len(second) - 1)}"

    def _get_hints_used(self, user_id: int, day: str) -> int:
        if self._store is not None:
            return self._store.hints_used(user_id, day)
        return self._hints_used.get((user_id, day), 0)

    def _record_hint(self, user_id: int, day: str) -> None:
        if self._store is not None:
            self._store.record_hint(user_id, day)
        else:
            self._hints_used[(user_id, day)] = self._hints_used.get((user_id, day), 0) + 1

    def give_hint(self, channel_id: int, user_id: int) -> tuple[bool, str]:
        game = self._active_game
        if game is None or self._active_channel_id != channel_id:
            return False, self._tr(
                "❌ **No active word-chain game in this channel**",
                "❌ **Không có trò chơi nối từ nào đang hoạt động trong kênh này**",
            )

        day = datetime.now(VIETNAM_TZ).date().isoformat()
        used = self._get_hints_used(user_id, day)
        if used >= self.HINTS_PER_DAY:
            return False, self._tr(
                f"❌ **You have used all {self.HINTS_PER_DAY} hints for today**\n> Hints reset at midnight (Vietnam time).",
                f"❌ **Bạn đã dùng hết {self.HINTS_PER_DAY} lượt gợi ý hôm nay**\n> Lượt gợi ý được làm mới lúc 0 giờ (giờ Việt Nam).",
            )

        if game.language == "vi":
            options = self._vi_dictionary.candidates(game.expected_start_key, game.used)
            if not options:
                return False, self._tr(
                    "❌ **No word can follow anymore**",
                    "❌ **Không còn từ nào để nối tiếp**",
                )

            hint = self._mask_hint(self._vi_dictionary.words[random.choice(options)])
            self._record_hint(user_id, day)
            remaining = self.HINTS_PER_DAY - used - 1
            return True, self._tr(
                f"💡 **Hint:** `{hint}`\n"
                f"> **{len(options)}** word(s) can follow `{game.expected_start_word}`.\n"
                f"> Hints left today: **{remaining}/{self.HINTS_PER_DAY}**",
                f"💡 **Gợi ý:** `{hint}`\n"
                f"> Có **{len(options)}** từ có thể nối tiếp `{game.expected_start_word}`.\n"
                f"> Lượt gợi ý còn lại hôm nay: **{remaining}/{self.HINTS_PER_DAY}**",
            )

        # English: the shared daily counter is used the same way.
        if not len(self._en_dictionary):
            return False, self._en_unavailable_message()
        letter = game.expected_start_key
        count = self._en_dictionary.count_continuations(letter, game.used)
        word = self._en_dictionary.example(letter, game.used) if count else None
        if word is None:
            return False, self._tr(
                "❌ **No word can follow anymore**",
                "❌ **Không còn từ nào để nối tiếp**",
            )

        hint = mask_en_word(word)
        self._record_hint(user_id, day)
        remaining = self.HINTS_PER_DAY - used - 1
        return True, self._tr(
            f"💡 **Hint:** `{hint}`\n"
            f"> **{count}** word(s) can follow (starting with `{letter}`).\n"
            f"> Hints left today: **{remaining}/{self.HINTS_PER_DAY}**",
            f"💡 **Gợi ý:** `{hint}`\n"
            f"> Có **{count}** từ có thể nối tiếp (bắt đầu bằng chữ `{letter}`).\n"
            f"> Lượt gợi ý còn lại hôm nay: **{remaining}/{self.HINTS_PER_DAY}**",
        )

    # ---------- vote skip ----------
    def vote_skip(self, channel_id: int, user_id: int, user_name: str) -> tuple[str, str]:
        """Vote to skip the current word. Enough votes reveal an answer and start a new round.

        Returns (SkipStatus, message).
        """
        game = self._active_game
        if game is None or self._active_channel_id != channel_id:
            return SkipStatus.ERROR, self._tr(
                "❌ **No active word-chain game in this channel**",
                "❌ **Không có trò chơi nối từ nào đang hoạt động trong kênh này**",
            )

        needed = self.SKIP_VOTES_NEEDED
        if user_id in self._skip_votes:
            return SkipStatus.ERROR, self._tr(
                f"ℹ️ **You already voted to skip** ({len(self._skip_votes)}/{needed})",
                f"ℹ️ **Bạn đã bỏ phiếu bỏ qua rồi** ({len(self._skip_votes)}/{needed})",
            )

        self._skip_votes.add(user_id)
        votes = len(self._skip_votes)
        if votes < needed:
            missing = needed - votes
            return SkipStatus.VOTED, self._tr(
                f"🗳️ **{user_name} voted to skip** ({votes}/{needed})\n"
                f"> **{missing}** more player(s) must vote to skip `{game.expected_start_word}`.",
                f"🗳️ **{user_name} muốn bỏ qua** ({votes}/{needed})\n"
                f"> Cần thêm **{missing}** người bỏ phiếu để bỏ qua `{game.expected_start_word}`.",
            )

        if game.language == "vi":
            skip_text = self._tr(
                f"⏭️ **Skipped!** Nobody could continue `{game.expected_start_word}`.",
                f"⏭️ **Đã bỏ qua!** Không ai nối được `{game.expected_start_word}`.",
            )
            answer = self._vi_example_answer(game)
            if answer:
                skip_text += self._tr(
                    f"\n> Could have continued with: `{answer}`",
                    f"\n> Có thể nối bằng: `{answer}`",
                )
        else:
            skip_text = self._tr(
                f"⏭️ **Skipped!** Nobody found a word starting with `{game.expected_start_word}`.",
                f"⏭️ **Đã bỏ qua!** Không ai tìm được từ bắt đầu bằng chữ `{game.expected_start_word}`.",
            )
            answer = self._en_example_answer(game)
            if answer:
                skip_text += self._tr(
                    f"\n> Could have continued with: `{answer}`",
                    f"\n> Có thể nối bằng: `{answer}`",
                )

        new_game = self._new_state(game.language)
        self._skip_votes.clear()
        self._last_answer_at.clear()
        if new_game is None:
            self._active_channel_id = None
            self._active_game = None
            self._save()
            return SkipStatus.SKIPPED, skip_text
        self._active_game = new_game
        self._save()
        return SkipStatus.SKIPPED, skip_text + "\n\n" + self._round_intro(
            new_game, self._tr("🎮 **New round!**", "🎮 **Lượt chơi mới!**")
        )

    # ---------- turns ----------
    def _check_cooldown(self, key: tuple[int, int], user_name: str, now: float) -> PhraseResult | None:
        last_answer_at = self._last_answer_at.get(key)
        if last_answer_at is not None:
            elapsed = now - last_answer_at
            if elapsed < self.COOLDOWN_SECONDS:
                remaining = self.COOLDOWN_SECONDS - elapsed
                return PhraseResult(
                    PhraseStatus.COOLDOWN,
                    self._tr(
                        f"⏳ **{user_name} is on cooldown**\n> Please wait **{remaining:.1f}s** before your next answer.",
                        f"⏳ **{user_name} đang trong cooldown**\n> Vui lòng chờ **{remaining:.1f}s** trước khi trả lời tiếp.",
                    ),
                )
        return None

    def _used_message(self, used_by: str | None) -> str:
        if used_by is None:
            return self._tr(
                "❌ **This word/phrase was already used before**\n> It was the starter word",
                "❌ **Từ/cụm từ đã được dùng trước đó**\n> Đây là từ bắt đầu",
            )
        return self._tr(
            f"❌ **This word/phrase was already used before**\n> Used by **{used_by}**",
            f"❌ **Từ/cụm từ đã được dùng trước đó**\n> Đã dùng bởi **{used_by}**",
        )

    def _accept(
        self,
        game: WordChainState,
        used_key: str,
        phrase: str,
        last_word: str,
        last_key: str,
        key: tuple[int, int],
        user_id: int,
        user_name: str,
        now: float,
    ) -> None:
        game.current_phrase = phrase
        game.expected_start_word = last_word
        game.expected_start_key = last_key
        game.used[used_key] = user_name
        game.last_player_id = user_id
        game.turns += 1
        self._last_answer_at[key] = now
        self._skip_votes.clear()  # the chain moved on, so earlier skip votes no longer apply
        self._bump(user_id, "correct", game.language)

    def handle_player_phrase(
        self,
        channel_id: int,
        user_id: int,
        user_name: str,
        text: str,
    ) -> PhraseResult | None:
        """Handle a message in the game channel. Returns None when the message is ignored."""
        game = self._active_game
        if game is None or self._active_channel_id != channel_id:
            return None

        if game.language == "vi":
            result = self._handle_vietnamese_phrase(game, channel_id, user_id, user_name, text)
        else:
            result = self._handle_english_phrase(game, channel_id, user_id, user_name, text)

        if result is not None and result.status in (
            PhraseStatus.WRONG_START,
            PhraseStatus.NOT_IN_DICT,
            PhraseStatus.USED,
            PhraseStatus.BANNED,
        ):
            self._bump(user_id, "wrong", game.language)
        if result is not None and result.accepted:
            self._save()
        return result

    def _handle_vietnamese_phrase(
        self,
        game: WordChainState,
        channel_id: int,
        user_id: int,
        user_name: str,
        text: str,
    ) -> PhraseResult | None:
        # Anything that is not exactly 2 syllables is treated as chat and ignored.
        parsed = parse_word(text)
        if parsed is None or not len(self._vi_dictionary):
            return None
        word_key, display = parsed
        first_key, last_key = word_key.split(" ")
        last_word = display.split(" ")[-1]

        now = time.monotonic()
        key = (channel_id, user_id)
        cooldown_error = self._check_cooldown(key, user_name, now)
        if cooldown_error is not None:
            return cooldown_error

        if first_key != game.expected_start_key:
            return PhraseResult(
                PhraseStatus.WRONG_START,
                self._tr(
                    f"❌ **Wrong start word**\n> Your word must start with: `{game.expected_start_word}`",
                    f"❌ **Sai từ bắt đầu**\n> Từ của bạn phải bắt đầu bằng: `{game.expected_start_word}`",
                ),
            )

        if word_key not in self._vi_dictionary:
            language_label = self._label_for_language(game.language)
            return PhraseResult(
                PhraseStatus.NOT_IN_DICT,
                self._tr(
                    f"❌ **Unknown word for {language_label} dictionary**\n> **{display}**",
                    f"❌ **Từ không tồn tại trong từ điển {language_label}**\n> **{display}**",
                ),
            )

        if word_key in game.used:
            return PhraseResult(PhraseStatus.USED, self._used_message(game.used[word_key]))

        self._accept(game, word_key, display, last_word, last_key, key, user_id, user_name, now)

        if self._vi_dictionary.has_continuation(last_key, game.used):
            return PhraseResult(
                PhraseStatus.OK,
                self._tr(
                    f"✅ **Correct**\n> Next word must start with: `{last_word}`",
                    f"✅ **Chính xác**\n> Từ tiếp theo phải bắt đầu bằng: `{last_word}`",
                ),
            )

        # Dead end: nobody can continue -> this player wins and a new round starts.
        self._bump(user_id, "wins", game.language)
        win_text = self._tr(
            f"🏆 **{user_name} wins with `{display}`!**\n"
            f"> No word starts with `{last_word}` anymore.\n"
            f"> The round lasted **{game.turns}** turn(s).",
            f"🏆 **{user_name} chiến thắng với từ `{display}`!**\n"
            f"> Không còn từ nào bắt đầu bằng `{last_word}`.\n"
            f"> Lượt chơi kéo dài **{game.turns}** lượt nối.",
        )
        new_game = self._new_state(game.language)
        if new_game is None:
            self._active_channel_id = None
            self._active_game = None
            self._save()
            return PhraseResult(PhraseStatus.WIN, win_text)

        self._active_game = new_game
        self._last_answer_at.clear()
        self._skip_votes.clear()
        return PhraseResult(
            PhraseStatus.WIN,
            win_text + "\n\n" + self._round_intro(new_game, self._tr("🎮 **New round!**", "🎮 **Lượt chơi mới!**")),
        )

    def _handle_english_phrase(
        self,
        game: WordChainState,
        channel_id: int,
        user_id: int,
        user_name: str,
        text: str,
    ) -> PhraseResult | None:
        # Anything that is not one English word of 3+ letters is treated as chat and ignored.
        word = parse_en_word(text)
        if word is None or not len(self._en_dictionary):
            return None

        # Offensive words are refused before any other check: the chain, the used list and
        # the player's cooldown stay as they are.
        if is_offensive(word):
            return PhraseResult(
                PhraseStatus.BANNED,
                self._tr(
                    f"⚠️ **Warning, {user_name}: that word is not allowed**\n"
                    "> Offensive words are banned in this game. Please keep the chat friendly.",
                    f"⚠️ **Cảnh báo {user_name}: từ này không được phép dùng**\n"
                    "> Từ ngữ xúc phạm bị cấm trong trò chơi. Hãy giữ không khí thân thiện nhé.",
                ),
            )

        # A word with the wrong first letter is chat ("thanks", unaccented Vietnamese like
        # "roi"), not an attempt: ignore it without a reaction, a stat or a cooldown check.
        if word[0] != game.expected_start_key:
            return None

        now = time.monotonic()
        key = (channel_id, user_id)
        cooldown_error = self._check_cooldown(key, user_name, now)
        if cooldown_error is not None:
            return cooldown_error

        if word not in self._en_dictionary:
            language_label = self._label_for_language(game.language)
            return PhraseResult(
                PhraseStatus.NOT_IN_DICT,
                self._tr(
                    f"❌ **Unknown word for {language_label} dictionary**\n> **{word}**",
                    f"❌ **Từ không tồn tại trong từ điển {language_label}**\n> **{word}**",
                ),
            )

        if word in game.used:
            return PhraseResult(PhraseStatus.USED, self._used_message(game.used[word]))

        last = word[-1]
        self._accept(game, word, word, last, last, key, user_id, user_name, now)

        if self._en_dictionary.has_continuation(last, game.used):
            return PhraseResult(
                PhraseStatus.OK,
                self._tr(
                    f"✅ **Correct**\n> Next word must start with the letter: `{last}`",
                    f"✅ **Chính xác**\n> Từ tiếp theo phải bắt đầu bằng chữ: `{last}`",
                ),
            )

        # Dead end: nobody can continue -> this player wins and a new round starts.
        self._bump(user_id, "wins", game.language)
        win_text = self._tr(
            f"🏆 **{user_name} wins with `{word}`!**\n"
            f"> No unused word starts with the letter `{last}`.\n"
            f"> The round lasted **{game.turns}** turn(s).",
            f"🏆 **{user_name} chiến thắng với từ `{word}`!**\n"
            f"> Không còn từ nào bắt đầu bằng chữ `{last}`.\n"
            f"> Lượt chơi kéo dài **{game.turns}** lượt nối.",
        )
        new_game = self._new_state(game.language)
        if new_game is None:
            self._active_channel_id = None
            self._active_game = None
            self._save()
            return PhraseResult(PhraseStatus.WIN, win_text)

        self._active_game = new_game
        self._last_answer_at.clear()
        self._skip_votes.clear()
        return PhraseResult(
            PhraseStatus.WIN,
            win_text + "\n\n" + self._round_intro(new_game, self._tr("🎮 **New round!**", "🎮 **Lượt chơi mới!**")),
        )

    # ---------- stats ----------
    def player_profile(self, user_id: int, user_name: str) -> str:
        """Profile with one section per language (the default language first)."""
        if self._store is None:
            return self._tr("❌ **Stats are not available**", "❌ **Không có dữ liệu thống kê**")
        header = self._tr(
            f"👤 **Word-chain profile of {user_name}**",
            f"👤 **Hồ sơ nối từ của {user_name}**",
        )
        languages = sorted(LANGUAGE_LABELS, key=lambda lang: lang != self._default_language)
        return "\n".join([header] + [self._profile_section(user_id, lang) for lang in languages])

    def _profile_section(self, user_id: int, language: str) -> str:
        title = f"🌐 **{self._label_for_language(language)}**"
        player = self._store.get_player(user_id, language)
        if not (player["correct"] or player["wrong"] or player["wins"]):
            return title + "\n" + self._tr("> No games yet", "> Chưa chơi")
        total = player["correct"] + player["wrong"]
        accuracy = f"{player['correct'] / total:.0%}" if total else "—"
        rank = self._store.rank_of(user_id, language)
        rank_text = f"#{rank}" if rank else self._tr("Unranked", "Chưa xếp hạng")
        return title + "\n" + self._tr(
            f"> 🏆 **Wins:** `{player['wins']}`\n"
            f"> ✅ **Correct:** `{player['correct']}`\n"
            f"> ❌ **Wrong:** `{player['wrong']}`\n"
            f"> 🎯 **Accuracy:** `{accuracy}`\n"
            f"> 📊 **Rank:** `{rank_text}`",
            f"> 🏆 **Thắng:** `{player['wins']}`\n"
            f"> ✅ **Từ đúng:** `{player['correct']}`\n"
            f"> ❌ **Từ sai:** `{player['wrong']}`\n"
            f"> 🎯 **Độ chính xác:** `{accuracy}`\n"
            f"> 📊 **Hạng:** `{rank_text}`",
        )

    def leaderboard(self, language: str | None = None, limit: int = 20) -> str:
        """Top players of one language: the given one, else the running game's, else the default."""
        if language is not None:
            selected = self.normalize_language(language)
            if selected is None:
                return self._unsupported_language_message()
        else:
            selected = self._active_game.language if self._active_game is not None else self._default_language
        label = self._label_for_language(selected)
        rows = self._store.top(selected, limit) if self._store is not None else []
        if not rows:
            return self._tr(
                f"📭 **No {label} games played yet**\n> Start a game and be the first!",
                f"📭 **Chưa có ai chơi nối từ {label}**\n> Hãy bắt đầu trò chơi và là người đầu tiên!",
            )
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [
            f"{medals.get(i, f'`#{i:>2}`')} <@{row['user_id']}> — "
            + self._tr(
                f"**{row['wins']}** wins · {row['correct']} correct",
                f"**{row['wins']}** thắng · {row['correct']} từ đúng",
            )
            for i, row in enumerate(rows, 1)
        ]
        return self._tr(
            f"🏅 **Word-chain leaderboard: {label}**\n",
            f"🏅 **Bảng xếp hạng nối từ: {label}**\n",
        ) + "\n".join(lines)

    # ---------- dictionary tools ----------
    def check_word(self, text: str, language: str | None = None) -> tuple[bool, str]:
        if language is None:
            # Route by shape like add_word: one a-z token -> English, several tokens ->
            # Vietnamese; anything else uses the active game's (or the default) language.
            if parse_en_word(text, fold_accents=False) is not None:
                language = "en"
            elif any(ch.isspace() for ch in text.strip()):
                language = "vi"
            else:
                language = self._active_game.language if self._active_game is not None else self._default_language

        if language == "vi":
            parsed = parse_word(text)
            if parsed is None:
                return False, self._tr(
                    "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)",
                    "❌ **Từ phải gồm đúng 2 âm tiết** (chỉ có chữ cái)",
                )
            word_key, display = parsed
            if word_key not in self._vi_dictionary:
                return False, self._tr(
                    f"❌ **{display}** is not in the dictionary.",
                    f"❌ **{display}** không có trong từ điển.",
                )
            count = len(self._vi_dictionary.candidates(word_key.split(" ")[1]))
            return True, self._tr(
                f"✅ **{display}** is in the dictionary. **{count}** word(s) can follow it.",
                f"✅ **{display}** có trong từ điển. Có **{count}** từ có thể nối tiếp.",
            )

        word = parse_en_word(text)
        if word is None:
            return False, self._tr(
                "❌ **An English word must be one word of 3+ letters (a–z only)**",
                "❌ **Từ tiếng Anh phải là một từ, từ 3 chữ cái trở lên (chỉ a–z)**",
            )
        if not len(self._en_dictionary):
            return False, self._en_unavailable_message()
        if is_offensive(word):
            return False, self._tr(f"🚫 **{word}** is banned in games.", f"🚫 **{word}** bị cấm trong trò chơi.")
        if word not in self._en_dictionary:
            return False, self._tr(
                f"❌ **{word}** is not in the dictionary.",
                f"❌ **{word}** không có trong từ điển.",
            )
        count = self._en_dictionary.count_continuations(word[-1])
        return True, self._tr(
            f"✅ **{word}** is in the dictionary. **{count}** word(s) can follow it (starting with `{word[-1]}`).",
            f"✅ **{word}** có trong từ điển. Có **{count}** từ có thể nối tiếp (bắt đầu bằng chữ `{word[-1]}`).",
        )

    def _invalid_word_shape_message(self) -> str:
        return self._tr(
            "❌ **Invalid word**\n"
            "> English: one word, 3+ letters (a–z). Vietnamese: exactly 2 syllables (letters only).",
            "❌ **Từ không hợp lệ**\n"
            "> Tiếng Anh: một từ, từ 3 chữ cái trở lên (a–z). Tiếng Việt: đúng 2 âm tiết (chỉ có chữ cái).",
        )

    def _add_english_word(self, word: str, user_id: int) -> tuple[bool, str]:
        if not len(self._en_dictionary):
            return False, self._en_unavailable_message()
        if is_offensive(word):
            return False, self._tr(
                f"❌ **{word}** is on the banned word list and can't be added.",
                f"❌ **{word}** nằm trong danh sách từ cấm nên không thể thêm.",
            )
        if word in self._en_dictionary:
            return False, self._tr(
                f"ℹ️ **{word}** is already in the dictionary.",
                f"ℹ️ **{word}** đã có trong từ điển rồi.",
            )
        self._en_dictionary.add(word)
        if self._store is not None:
            self._store.set_custom_word(word, True, user_id)
        return True, self._tr(
            f"✅ Added **{word}** to the English dictionary.",
            f"✅ Đã thêm **{word}** vào từ điển tiếng Anh.",
        )

    def _remove_english_word(self, word: str, user_id: int) -> tuple[bool, str]:
        if self._en_dictionary.remove(word) is None:
            return False, self._tr(
                "❌ This word is not in the dictionary.",
                "❌ Không tìm thấy từ này trong từ điển.",
            )
        if self._store is not None:
            self._store.set_custom_word(word, False, user_id)
        return True, self._tr(
            f"🗑️ Removed **{word}** from the English dictionary.",
            f"🗑️ Đã xoá **{word}** khỏi từ điển tiếng Anh.",
        )

    def add_word(self, text: str, user_id: int) -> tuple[bool, str]:
        # Routing by shape: one a-z token ("apple", also "hoa") -> English. Accents are kept,
        # so "học" is not English and fails the Vietnamese check too -> shape message.
        # Several tokens ("ice cream", "học sinh") -> Vietnamese, as before.
        en_word = parse_en_word(text, fold_accents=False)
        if en_word is not None:
            return self._add_english_word(en_word, user_id)
        parsed = parse_word(text)
        if parsed is None:
            if not any(ch.isspace() for ch in text.strip()):
                return False, self._invalid_word_shape_message()
            return False, self._tr(
                "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)",
                "❌ **Từ phải gồm đúng 2 âm tiết** (chỉ có chữ cái)",
            )
        word_key, display = parsed
        if word_key in self._vi_dictionary:
            return False, self._tr(
                f"ℹ️ **{display}** is already in the dictionary.",
                f"ℹ️ **{display}** đã có trong từ điển rồi.",
            )
        self._vi_dictionary.add(display)
        if self._store is not None:
            self._store.set_custom_word(display, True, user_id)
        return True, self._tr(
            f"✅ Added **{display}** to the dictionary.",
            f"✅ Đã thêm **{display}** vào từ điển.",
        )

    def remove_word(self, text: str, user_id: int) -> tuple[bool, str]:
        en_word = parse_en_word(text, fold_accents=False)  # same routing as add_word
        if en_word is not None:
            return self._remove_english_word(en_word, user_id)
        parsed = parse_word(text)
        if parsed is None or self._vi_dictionary.remove(parsed[1]) is None:
            return False, self._tr(
                "❌ This word is not in the dictionary.",
                "❌ Không tìm thấy từ này trong từ điển.",
            )
        if self._store is not None:
            self._store.set_custom_word(parsed[1], False, user_id)
        return True, self._tr(
            f"🗑️ Removed **{parsed[1]}** from the dictionary.",
            f"🗑️ Đã xoá **{parsed[1]}** khỏi từ điển.",
        )
