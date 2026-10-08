import json
import logging
import random
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from wordfreq import top_n_list, zipf_frequency

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
    WIN = "win"                  # accepted and nothing can follow -> player wins (Vietnamese only)
    INVALID = "invalid"          # not a usable phrase
    SAME_PLAYER = "same_player"  # same player answered twice in a row
    COOLDOWN = "cooldown"        # player is still on cooldown
    WRONG_START = "wrong_start"  # does not start with the expected word
    NOT_IN_DICT = "not_in_dict"  # unknown word(s)
    USED = "used"                # already used in this game


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
    # Comparison key of the expected start word: casefolded word (en) or syllable key (vi).
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
    _STARTER_WORD_POOL_SIZE = 5000
    _MEANINGFUL_WORD_MIN_ZIPF = 2.5
    # Each syllable of a Vietnamese starter word must be at least this common.
    _VI_STARTER_SYLLABLE_MIN_ZIPF = 3.5

    def __init__(
        self,
        default_language: str = "en",
        ui_language: str | None = None,
        store: GameStore | None = None,
        vi_dictionary: VietnameseDictionary | None = None,
    ) -> None:
        self._active_channel_id: int | None = None
        self._active_game: WordChainState | None = None
        self._last_answer_at: dict[tuple[int, int], float] = {}
        normalized_default = self.normalize_language(default_language)
        self._default_language = normalized_default or "en"
        normalized_ui = self.normalize_language(ui_language) if ui_language is not None else None
        self._ui_language = normalized_ui or self._default_language
        self._starter_word_cache: dict[str, list[str]] = {}
        self._store = store
        self._vi_dictionary = (
            vi_dictionary
            if vi_dictionary is not None
            else VietnameseDictionary(is_common_starter=self._is_common_vi_starter)
        )

        if self._store is not None:
            saved = self._store.load_game()
            if saved is not None:
                self._active_channel_id, state_json = saved
                self._active_game = WordChainState.from_json(state_json)
                if self._active_game.language == "vi":
                    # Keys may come from an older normalization: rebuild the one that matters.
                    self._active_game.expected_start_key = syllable_key(self._active_game.expected_start_word)

    @property
    def is_vietnamese_ui(self) -> bool:
        return self._ui_language == "vi"

    @property
    def vi_dictionary(self) -> VietnameseDictionary:
        return self._vi_dictionary

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

    def apply_custom_words(self) -> None:
        """Apply admin dictionary edits saved in the database."""
        if self._store is None:
            return
        for word, added in self._store.custom_words():
            if added:
                self._vi_dictionary.add(word)
            else:
                self._vi_dictionary.remove(word)

    # ---------- English helpers ----------
    @staticmethod
    def _extract_edge_words(text: str) -> tuple[str, str] | None:
        words = re.findall(r"[^\W_]+(?:['-][^\W_]+)*", text.casefold(), flags=re.UNICODE)
        if not words:
            return None
        return words[0], words[-1]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[^\W_]+(?:['-][^\W_]+)*", text.casefold(), flags=re.UNICODE)

    @staticmethod
    def _normalize_phrase(text: str) -> str:
        return " ".join(WordChainGameManager._tokenize(text))

    @staticmethod
    def _word_count(text: str) -> int:
        return len(WordChainGameManager._tokenize(text))

    @staticmethod
    def _is_word_in_dictionary(word: str, language: str) -> bool:
        # zipf_frequency returns 0 when the token is unknown for a language.
        return zipf_frequency(word, language) > 0

    @staticmethod
    def _is_single_word(token: str) -> bool:
        return (
            re.fullmatch(r"[^\W_]+(?:['-][^\W_]+)*", token, flags=re.UNICODE)
            is not None
        )

    def _invalid_words(self, text: str, language: str) -> list[str]:
        words = self._tokenize(text)
        return [word for word in words if not self._is_word_in_dictionary(word, language)]

    def _get_starter_word_pool(self, language: str) -> list[str]:
        cached = self._starter_word_cache.get(language)
        if cached is not None:
            return cached

        candidates = top_n_list(language, self._STARTER_WORD_POOL_SIZE)
        word_pool = [
            word
            for word in candidates
            if self._is_single_word(word)
            and len(word) >= 2
            and zipf_frequency(word, language) >= self._MEANINGFUL_WORD_MIN_ZIPF
            and word.casefold() not in STARTER_BLACKLIST.get(language, set())
        ]
        self._starter_word_cache[language] = word_pool
        return word_pool

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

        starter_pool = self._get_starter_word_pool(language)
        if not starter_pool:
            return None
        starter_word = random.choice(starter_pool)
        return WordChainState(
            current_phrase=starter_word,
            expected_start_word=starter_word,
            language=language,
            expected_start_key=starter_word.casefold(),
        )

    def _save(self) -> None:
        if self._store is None:
            return
        if self._active_game is None or self._active_channel_id is None:
            self._store.delete_game()
        else:
            self._store.save_game(self._active_channel_id, self._active_game.to_json())

    def _bump(self, user_id: int, stat: str) -> None:
        if self._store is not None:
            self._store.bump(user_id, stat)

    def _round_intro(self, game: WordChainState, title: str) -> str:
        language_label = self._label_for_language(game.language)
        if game.language == "vi":
            return self._tr(
                f"{title}\n"
                f"> **Language:** `{language_label}`\n"
                f"> **Starter word:** `{game.current_phrase}`\n"
                f"> **Next word must start with:** `{game.expected_start_word}`\n"
                "> **Rule:** exactly 2 syllables, no answering twice in a row\n"
                f"> **Cooldown per user:** `{int(self.COOLDOWN_SECONDS)}s`",
                f"{title}\n"
                f"> **Ngôn ngữ:** `{language_label}`\n"
                f"> **Từ bắt đầu:** `{game.current_phrase}`\n"
                f"> **Từ tiếp theo phải bắt đầu bằng:** `{game.expected_start_word}`\n"
                "> **Luật:** đúng 2 âm tiết, không nối 2 lần liên tiếp\n"
                f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
            )
        return self._tr(
            f"{title}\n"
            f"> **Language:** `{language_label}`\n"
            f"> **Starter word:** `{game.current_phrase}`\n"
            f"> **Next phrase must start with:** `{game.expected_start_word}`\n"
            f"> **Cooldown per user:** `{int(self.COOLDOWN_SECONDS)}s`",
            f"{title}\n"
            f"> **Ngôn ngữ:** `{language_label}`\n"
            f"> **Từ bắt đầu:** `{game.current_phrase}`\n"
            f"> **Cụm tiếp theo phải bắt đầu bằng:** `{game.expected_start_word}`\n"
            f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
        )

    def _vi_example_answer(self, game: WordChainState) -> str | None:
        options = self._vi_dictionary.candidates(game.expected_start_key, game.used)
        return self._vi_dictionary.words[random.choice(options)] if options else None

    def start_game(self, channel_id: int, language: str | None = None) -> tuple[bool, str]:
        if self._active_game is not None:
            return False, self._tr(
                "❌ **A word-chain game is already active**\n> Stop the current game before starting a new one.",
                "❌ **Đã có trò chơi nối từ đang hoạt động**\n> Hãy dừng trò chơi hiện tại trước khi bắt đầu trò mới.",
            )

        normalized_language = self.normalize_language(language)
        if language is not None and normalized_language is None:
            return False, self._tr(
                "❌ **Unsupported language**\n> Use `en` (English) or `vi` (Vietnamese).",
                "❌ **Ngôn ngữ không hỗ trợ**\n> Hãy dùng `en` (Tiếng Anh) hoặc `vi` (Tiếng Việt).",
            )
        selected_language = normalized_language or self._default_language

        game = self._new_state(selected_language)
        if game is None:
            return False, self._tr(
                "❌ **No suitable starter words available**\n> No valid starter word was found for the selected language.",
                "❌ **Không có từ bắt đầu phù hợp**\n> Không tìm thấy từ hợp lệ cho ngôn ngữ đã chọn.",
            )

        self._active_channel_id = channel_id
        self._active_game = game
        self._last_answer_at.clear()
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

        self._active_channel_id = None
        self._active_game = None
        self._last_answer_at.clear()
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

    # ---------- turns ----------
    def _check_turn_order(
        self, game: WordChainState, key: tuple[int, int], user_id: int, user_name: str, now: float
    ) -> PhraseResult | None:
        if game.last_player_id == user_id:
            return PhraseResult(
                PhraseStatus.SAME_PLAYER,
                self._tr(
                    "⏳ **You just answered**\n> Wait for someone else to continue the chain.",
                    "⏳ **Bạn vừa nối rồi**\n> Chờ người khác nối tiếp nhé!",
                ),
            )

        last_answer_at = self._last_answer_at.get(key)
        if last_answer_at is not None:
            elapsed = now - last_answer_at
            if elapsed < self.COOLDOWN_SECONDS:
                remaining = self.COOLDOWN_SECONDS - elapsed
                return PhraseResult(
                    PhraseStatus.COOLDOWN,
                    self._tr(
                        f"❌ **{user_name} is on cooldown**\n> Please wait **{remaining:.1f}s** before your next answer.",
                        f"❌ **{user_name} đang trong cooldown**\n> Vui lòng chờ **{remaining:.1f}s** trước khi trả lời tiếp.",
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
        self._bump(user_id, "correct")

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
        ):
            self._bump(user_id, "wrong")
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
        turn_error = self._check_turn_order(game, key, user_id, user_name, now)
        if turn_error is not None:
            return turn_error

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
        self._bump(user_id, "wins")
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
        now = time.monotonic()
        key = (channel_id, user_id)

        normalized_phrase = self._normalize_phrase(text)
        if not normalized_phrase:
            return PhraseResult(
                PhraseStatus.INVALID,
                self._tr("❌ **Please send a word or phrase**", "❌ **Vui lòng gửi một từ hoặc cụm từ**"),
            )

        if self._word_count(text) < 2:
            return PhraseResult(
                PhraseStatus.INVALID,
                self._tr(
                    "❌ **The phrase must have at least 2 words**\n> Not just the last word again.",
                    "❌ **Cụm từ phải có ít nhất 2 từ**\n> Không chỉ lặp lại từ cuối.",
                ),
            )

        if normalized_phrase in game.used:
            return PhraseResult(PhraseStatus.USED, self._used_message(game.used[normalized_phrase]))

        turn_error = self._check_turn_order(game, key, user_id, user_name, now)
        if turn_error is not None:
            return turn_error

        edge_words = self._extract_edge_words(text)
        if edge_words is None:
            return PhraseResult(
                PhraseStatus.INVALID,
                self._tr("❌ **Please send a word or phrase**", "❌ **Vui lòng gửi một từ hoặc cụm từ**"),
            )

        first_word, last_word = edge_words
        if first_word != game.expected_start_key:
            return PhraseResult(
                PhraseStatus.WRONG_START,
                self._tr(
                    f"❌ **Wrong start word**\n> Your phrase must start with: `{game.expected_start_word}`",
                    f"❌ **Sai từ bắt đầu**\n> Cụm từ của bạn phải bắt đầu bằng: `{game.expected_start_word}`",
                ),
            )

        invalid_words = self._invalid_words(text, game.language)
        if invalid_words:
            language_label = self._label_for_language(game.language)
            unknown_text = ", ".join(f"**{word}**" for word in invalid_words)
            return PhraseResult(
                PhraseStatus.NOT_IN_DICT,
                self._tr(
                    f"❌ **Unknown word(s) for {language_label} dictionary**\n> {unknown_text}",
                    f"❌ **Từ không tồn tại trong từ điển {language_label}**\n> {unknown_text}",
                ),
            )

        self._accept(game, normalized_phrase, text.strip(), last_word, last_word, key, user_id, user_name, now)
        return PhraseResult(
            PhraseStatus.OK,
            self._tr(
                f"✅ **Correct**\n> Next phrase must start with: `{last_word}`",
                f"✅ **Chính xác**\n> Cụm từ tiếp theo phải bắt đầu bằng: `{last_word}`",
            ),
        )

    # ---------- stats ----------
    def player_profile(self, user_id: int, user_name: str) -> str:
        if self._store is None:
            return self._tr("❌ **Stats are not available**", "❌ **Không có dữ liệu thống kê**")
        player = self._store.get_player(user_id)
        total = player["correct"] + player["wrong"]
        accuracy = f"{player['correct'] / total:.0%}" if total else "—"
        rank = self._store.rank_of(user_id)
        rank_text = f"#{rank}" if rank else self._tr("Unranked", "Chưa xếp hạng")
        return self._tr(
            f"👤 **Word-chain profile of {user_name}**\n"
            f"> 🏆 **Wins:** `{player['wins']}`\n"
            f"> ✅ **Correct:** `{player['correct']}`\n"
            f"> ❌ **Wrong:** `{player['wrong']}`\n"
            f"> 🎯 **Accuracy:** `{accuracy}`\n"
            f"> 📊 **Rank:** `{rank_text}`",
            f"👤 **Hồ sơ nối từ của {user_name}**\n"
            f"> 🏆 **Thắng:** `{player['wins']}`\n"
            f"> ✅ **Từ đúng:** `{player['correct']}`\n"
            f"> ❌ **Từ sai:** `{player['wrong']}`\n"
            f"> 🎯 **Độ chính xác:** `{accuracy}`\n"
            f"> 📊 **Hạng:** `{rank_text}`",
        )

    def leaderboard(self, limit: int = 20) -> str:
        rows = self._store.top(limit) if self._store is not None else []
        if not rows:
            return self._tr(
                "📭 **Nobody has played yet**\n> Start a game and be the first!",
                "📭 **Chưa có ai chơi**\n> Hãy bắt đầu trò chơi và là người đầu tiên!",
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
        return self._tr("🏅 **Word-chain leaderboard**\n", "🏅 **Bảng xếp hạng nối từ**\n") + "\n".join(lines)

    # ---------- dictionary tools ----------
    def check_word(self, text: str, language: str | None = None) -> tuple[bool, str]:
        if language is None:
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

        normalized_phrase = self._normalize_phrase(text)
        if not normalized_phrase:
            return False, self._tr("❌ **Please send a word or phrase**", "❌ **Vui lòng gửi một từ hoặc cụm từ**")
        invalid_words = self._invalid_words(text, language)
        if invalid_words:
            unknown_text = ", ".join(f"**{word}**" for word in invalid_words)
            return False, self._tr(
                f"❌ **Unknown word(s):** {unknown_text}",
                f"❌ **Từ không có trong từ điển:** {unknown_text}",
            )
        return True, self._tr(
            f"✅ **{normalized_phrase}** is valid.",
            f"✅ **{normalized_phrase}** hợp lệ.",
        )

    def add_word(self, text: str, user_id: int) -> tuple[bool, str]:
        parsed = parse_word(text)
        if parsed is None:
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
