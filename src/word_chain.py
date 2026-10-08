import random
import re
import time
from dataclasses import dataclass

from wordfreq import top_n_list, zipf_frequency


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
        "ha noi",
        "hải phòng",
        "hai phong",
        "đà nẵng",
        "da nang",
        "huế",
        "hue",
        "sài gòn",
        "sai gon",
        "hồ chí minh",
        "ho chi minh",
        "nam",
        "lan",
        "hoa",
        "mai",
        "huong",
        "hương",
        "an",
        "bình",
        "binh",
        "thành",
        "thanh",
    },
}

VI_STARTER_WORDS = [
    "ăn",
    "bánh",
    "bạn",
    "biển",
    "bình",
    "cà",
    "cá",
    "cây",
    "chợ",
    "chó",
    "chùa",
    "công",
    "cửa",
    "đường",
    "gia",
    "giá",
    "giúp",
    "học",
    "hoa",
    "làng",
    "lúa",
    "mưa",
    "mùa",
    "nắng",
    "nước",
    "quê",
    "sách",
    "sông",
    "trăng",
    "trường",
    "vườn",
    "vui",
    "xanh",
    "yêu",
    "đi",
    "đẹp",
    "đời",
    "đất",
    "ở",
    "ôm",
    "ngày",
    "nhà",
    "tình",
    "người",
    "việt",
    "tiếng",
    "mắt",
    "tay",
    "chân",
    "tim",
    "lửa",
    "gió",
    "mặt",
    "trời",
    "thu",
    "xuân",
    "hè",
    "đông",
    "bầu",
    "trời",
    "cánh",
    "hoa",
    "sen",
    "cờ",
    "vua",
    "bài",
    "hát",
    "nhạc",
    "đèn",
    "phố",
    "thành",
    "phố",
    "mẹ",
    "cha",
    "con",
    "em",
    "anh",
    "chị",
    "bác",
    "cô",
    "thầy",
    "trò",
]


@dataclass
class WordChainState:
    current_phrase: str
    expected_start_word: str
    language: str


class WordChainGameManager:
    COOLDOWN_SECONDS = 5.0
    _STARTER_WORD_POOL_SIZE = 5000
    _MEANINGFUL_WORD_MIN_ZIPF = 2.5

    def __init__(self, default_language: str = "en", ui_language: str | None = None) -> None:
        self._active_channel_id: int | None = None
        self._active_game: WordChainState | None = None
        self._last_answer_at: dict[tuple[int, int], float] = {}
        self._used_phrases: dict[str, str] = {}
        normalized_default = self.normalize_language(default_language)
        self._default_language = normalized_default or "en"
        normalized_ui = self.normalize_language(ui_language) if ui_language is not None else None
        self._ui_language = normalized_ui or self._default_language
        self._starter_word_cache: dict[str, list[str]] = {}

    @property
    def is_vietnamese_ui(self) -> bool:
        return self._ui_language == "vi"

    def _label_for_language(self, language: str) -> str:
        if self.is_vietnamese_ui:
            return LANGUAGE_LABELS_VI.get(language, language)
        return LANGUAGE_LABELS.get(language, language)

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

        if language == "vi":
            word_pool = [
                word
                for word in VI_STARTER_WORDS
                if word.casefold() not in STARTER_BLACKLIST.get(language, set())
            ]
            self._starter_word_cache[language] = word_pool
            return word_pool

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

    def start_game(self, channel_id: int, language: str | None = None) -> tuple[bool, str]:
        if self._active_game is not None:
            if self.is_vietnamese_ui:
                return False, "❌ **Đã có trò chơi nối từ đang hoạt động**\n> Hãy dừng trò chơi hiện tại trước khi bắt đầu trò mới."
            return False, "❌ **A word-chain game is already active**\n> Stop the current game before starting a new one."

        normalized_language = self.normalize_language(language)
        if language is not None and normalized_language is None:
            if self.is_vietnamese_ui:
                return False, "❌ **Ngôn ngữ không hỗ trợ**\n> Hãy dùng `en` (Tiếng Anh) hoặc `vi` (Tiếng Việt)."
            return False, "❌ **Unsupported language**\n> Use `en` (English) or `vi` (Vietnamese)."
        selected_language = normalized_language or self._default_language

        starter_pool = self._get_starter_word_pool(selected_language)
        if not starter_pool:
            if self.is_vietnamese_ui:
                return False, "❌ **Không có từ bắt đầu phù hợp**\n> Không tìm thấy từ hợp lệ cho ngôn ngữ đã chọn."
            return False, "❌ **No suitable starter words available**\n> No valid starter word was found for the selected language."

        starter_word = random.choice(starter_pool)
        self._active_channel_id = channel_id
        self._active_game = WordChainState(
            current_phrase=starter_word,
            expected_start_word=starter_word,
            language=selected_language,
        )
        self._used_phrases.clear()
        self._last_answer_at.clear()
        language_label = self._label_for_language(selected_language)
        if self.is_vietnamese_ui:
            return (
                True,
                "✅ **Trò chơi nối từ đã bắt đầu**\n"
                f"> **Ngôn ngữ:** `{language_label}`\n"
                f"> **Từ bắt đầu:** `{starter_word}`\n"
                f"> **Cụm tiếp theo phải bắt đầu bằng:** `{starter_word}`\n"
                f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
            )
        return (
            True,
            "✅ **Word-chain game started**\n"
            f"> **Language:** `{language_label}`\n"
            f"> **Starter word:** `{starter_word}`\n"
            f"> **Next phrase must start with:** `{starter_word}`\n"
            f"> **Cooldown per user:** `{int(self.COOLDOWN_SECONDS)}s`",
        )

    def stop_game(self, channel_id: int) -> tuple[bool, str]:
        if self._active_game is None:
            if self.is_vietnamese_ui:
                return False, "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào."
            return False, "❌ **No active word-chain game**\n> There is no running game in this channel."

        if self._active_channel_id != channel_id:
            if self.is_vietnamese_ui:
                return False, "❌ **Trò chơi đang ở kênh khác**\n> Hãy dừng trò chơi tại kênh đã bắt đầu nó."
            return False, "❌ **The active game is in another channel**\n> Stop it from the channel where it started."

        self._active_channel_id = None
        self._active_game = None
        self._used_phrases.clear()
        self._last_answer_at.clear()
        if self.is_vietnamese_ui:
            return True, "✅ **Đã dừng trò chơi nối từ**"
        return True, "✅ **Word-chain game stopped**"

    def has_game(self, channel_id: int) -> bool:
        return self._active_game is not None and self._active_channel_id == channel_id

    def game_status(self, channel_id: int) -> tuple[bool, str]:
        game = self._active_game
        if game is None:
            if self.is_vietnamese_ui:
                return False, "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào."
            return False, "❌ **No active word-chain game in this channel**\n> There is no running game in this channel."

        if self._active_channel_id != channel_id:
            if self.is_vietnamese_ui:
                return False, "❌ **Trò chơi nối từ đang chạy ở kênh khác**"
            return False, "❌ **The active word-chain game is running in another channel**"

        language_label = self._label_for_language(game.language)
        if self.is_vietnamese_ui:
            return (
                True,
                "📋 **Trạng thái nối từ**\n"
                f"> **Ngôn ngữ:** `{language_label}`\n"
                f"> **Cụm từ hiện tại:** `{game.current_phrase}`\n"
                f"> **Từ bắt đầu yêu cầu:** `{game.expected_start_word}`\n"
                f"> **Cooldown mỗi người:** `{int(self.COOLDOWN_SECONDS)}s`",
            )
        return (
            True,
            "📋 **Word-chain status**\n"
            f"> **Language:** `{language_label}`\n"
            f"> **Current phrase:** `{game.current_phrase}`\n"
            f"> **Expected start word:** `{game.expected_start_word}`\n"
            f"> **Per-user cooldown:** `{int(self.COOLDOWN_SECONDS)}s`",
        )

    def handle_player_phrase(
        self,
        channel_id: int,
        user_id: int,
        user_name: str,
        text: str,
    ) -> tuple[bool, str] | None:
        game = self._active_game
        if game is None or self._active_channel_id != channel_id:
            return None

        now = time.monotonic()
        key = (channel_id, user_id)

        normalized_phrase = self._normalize_phrase(text)
        if not normalized_phrase:
            if self.is_vietnamese_ui:
                return False, "❌ **Vui lòng gửi một từ hoặc cụm từ**"
            return False, "❌ **Please send a word or phrase**"

        if self._word_count(text) < 2:
            if self.is_vietnamese_ui:
                return False, "❌ **Cụm từ phải có ít nhất 2 từ**\n> Không chỉ lặp lại từ cuối."
            return False, "❌ **The phrase must have at least 2 words**\n> Not just the last word again."

        used_by = self._used_phrases.get(normalized_phrase)
        if used_by is not None:
            if self.is_vietnamese_ui:
                return False, f"❌ **Từ/cụm từ đã được dùng trước đó**\n> Đã dùng bởi **{used_by}**"
            return False, f"❌ **This word/phrase was already used before**\n> Used by **{used_by}**"

        last_answer_at = self._last_answer_at.get(key)
        if last_answer_at is not None:
            elapsed = now - last_answer_at
            if elapsed < self.COOLDOWN_SECONDS:
                remaining = self.COOLDOWN_SECONDS - elapsed
                if self.is_vietnamese_ui:
                    return (
                        False,
                        f"❌ **{user_name} đang trong cooldown**\n> Vui lòng chờ **{remaining:.1f}s** trước khi trả lời tiếp.",
                    )
                return (
                    False,
                    f"❌ **{user_name} is on cooldown**\n> Please wait **{remaining:.1f}s** before your next answer.",
                )

        edge_words = self._extract_edge_words(text)
        if edge_words is None:
            if self.is_vietnamese_ui:
                return False, "❌ **Vui lòng gửi một từ hoặc cụm từ**"
            return False, "❌ **Please send a word or phrase**"

        first_word, last_word = edge_words
        if first_word != game.expected_start_word:
            if self.is_vietnamese_ui:
                return (
                    False,
                    "❌ **Sai từ bắt đầu**\n"
                    f"> Cụm từ của bạn phải bắt đầu bằng: `{game.expected_start_word}`",
                )
            return (
                False,
                "❌ **Wrong start word**\n"
                f"> Your phrase must start with: `{game.expected_start_word}`",
            )

        invalid_words = self._invalid_words(text, game.language)
        if invalid_words:
            language_label = self._label_for_language(game.language)
            unknown_text = ", ".join(f"**{word}**" for word in invalid_words)
            if self.is_vietnamese_ui:
                return (
                    False,
                    f"❌ **Từ không tồn tại trong từ điển {language_label}**\n> {unknown_text}",
                )
            return (
                False,
                f"❌ **Unknown word(s) for {language_label} dictionary**\n> {unknown_text}",
            )

        game.current_phrase = text.strip()
        game.expected_start_word = last_word
        self._used_phrases[normalized_phrase] = user_name
        self._last_answer_at[key] = now
        if self.is_vietnamese_ui:
            return True, f"✅ **Chính xác**\n> Cụm từ tiếp theo phải bắt đầu bằng: `{last_word}`"
        return True, f"✅ **Correct**\n> Next phrase must start with: `{last_word}`"
