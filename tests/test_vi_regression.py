"""Vietnamese mode regression tests: behavior and texts must not change with the English rework.

Expected strings are copied from the Vietnamese code paths as they were before the change.
Run from the repo root: python -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from en_dictionary import EnglishDictionary  # noqa: E402
from storage import GameStore  # noqa: E402
from vi_dictionary import VietnameseDictionary  # noqa: E402
from vi_text import parse_word, syllable_key  # noqa: E402
from word_chain import PhraseStatus, SkipStatus, WordChainGameManager, WordChainState  # noqa: E402


CH = 200
VI_WORDS = [
    "học sinh", "sinh viên", "viên chức", "chức năng",
    "hoà bình", "mỹ thuật", "lực sĩ",
]


def key(word: str) -> str:
    return parse_word(word)[0]


def make_vi(words=VI_WORDS) -> VietnameseDictionary:
    d = VietnameseDictionary()
    d.load_lines(words)
    d.build_start_pool()
    d._start_pool = [key("học sinh")]  # tiny dictionary: fix the starter word
    return d


def make_en() -> EnglishDictionary:
    d = EnglishDictionary()
    d.load_lines(["apple", "egg", "goat", "tiger", "sinh"])
    d.build_indexes()
    return d


class ViBase(unittest.TestCase):
    ui = "en"

    def setUp(self) -> None:
        self.store = GameStore(":memory:")
        self.vi = make_vi()
        self.en = make_en()
        self.mgr = self.manager()

    def manager(self) -> WordChainGameManager:
        return WordChainGameManager(
            default_language="vi",
            ui_language=self.ui,
            store=self.store,
            vi_dictionary=self.vi,
            en_dictionary=self.en,
        )

    def set_state(self, word="viên", phrase="sinh viên", used=None) -> WordChainState:
        game = WordChainState(
            current_phrase=phrase,
            expected_start_word=word,
            language="vi",
            expected_start_key=syllable_key(word),
            used=dict(used) if used is not None else {key(phrase): None},
        )
        self.mgr._active_channel_id = CH
        self.mgr._active_game = game
        self.mgr._last_answer_at.clear()
        self.mgr._skip_votes.clear()
        return game

    def play(self, text, user_id=1, name="Bob"):
        return self.mgr.handle_player_phrase(CH, user_id, name, text)


class VietnameseRegressionEnUi(ViBase):
    def test_01_start_intro(self):
        ok, message = self.mgr.start_game(CH, "vi")
        self.assertTrue(ok)
        self.assertEqual(
            message,
            "✅ **Word-chain game started**\n"
            "> **Language:** `Vietnamese`\n"
            "> **Starter word:** `học sinh`\n"
            "> **Next word must start with:** `sinh`\n"
            "> **Rule:** exactly 2 syllables\n"
            "> **Cooldown per user:** `5s`",
        )
        game = self.mgr._active_game
        self.assertEqual(game.used, {key("học sinh"): None})
        self.assertEqual(game.expected_start_key, syllable_key("sinh"))

    def test_02_correct(self):
        self.mgr.start_game(CH, "vi")
        result = self.play("sinh viên")
        self.assertEqual(result.status, PhraseStatus.OK)
        self.assertEqual(result.message, "✅ **Correct**\n> Next word must start with: `viên`")
        self.assertEqual(self.store.get_player(1)["correct"], 1)

    def test_03_tone_and_iy_variants(self):
        self.set_state("hòa", "hài hòa", {})
        result = self.play("hoà bình")
        self.assertTrue(result.accepted, result)
        self.set_state("mĩ", "thẩm mĩ", {})
        result = self.play("mĩ thuật", user_id=2)
        self.assertTrue(result.accepted, result)
        self.assertIn("`mĩ thuật`", result.message)  # dead end: win message shows the typed spelling

    def test_04_errors(self):
        self.set_state()
        result = self.play("học sinh")
        self.assertEqual(result.status, PhraseStatus.WRONG_START)
        self.assertEqual(result.message, "❌ **Wrong start word**\n> Your word must start with: `viên`")
        result = self.play("viên xyz")
        self.assertEqual(result.status, PhraseStatus.NOT_IN_DICT)
        self.assertEqual(result.message, "❌ **Unknown word for Vietnamese dictionary**\n> **viên xyz**")
        self.set_state(used={key("sinh viên"): None, key("viên chức"): "Ann"})
        result = self.play("viên chức")
        self.assertEqual(result.status, PhraseStatus.USED)
        self.assertEqual(result.message, "❌ **This word/phrase was already used before**\n> Used by **Ann**")
        self.assertEqual(self.store.get_player(1)["wrong"], 3)

    def test_05_non_two_syllable_ignored(self):
        self.set_state()
        for text in ("apple", "viên", "một hai ba", "!chainstatus"):
            with self.subTest(text=text):
                self.assertIsNone(self.play(text))
        self.assertEqual(self.store.get_player(1), {"correct": 0, "wrong": 0, "wins": 0})

    def test_06_dead_end_win(self):
        self.set_state("chức", "viên chức")
        result = self.play("chức năng")
        self.assertEqual(result.status, PhraseStatus.WIN)
        self.assertTrue(
            result.message.startswith(
                "🏆 **Bob wins with `chức năng`!**\n"
                "> No word starts with `năng` anymore.\n"
                "> The round lasted **1** turn(s)."
            ),
            result.message,
        )
        self.assertIn(
            "\n\n🎮 **New round!**\n> **Language:** `Vietnamese`\n> **Starter word:** `học sinh`", result.message
        )
        self.assertEqual(self.store.get_player(1)["wins"], 1)

    def test_07_hint(self):
        self.set_state("lực", "nỗ lực")
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertTrue(ok)
        self.assertEqual(
            message,
            "💡 **Hint:** `lực s_`\n> **1** word(s) can follow `lực`.\n> Hints left today: **4/5**",
        )

    def test_08_skip(self):
        self.set_state()
        status, message = self.mgr.vote_skip(CH, 1, "Bob")
        self.assertEqual(status, SkipStatus.VOTED)
        self.assertEqual(
            message, "🗳️ **Bob voted to skip** (1/2)\n> **1** more player(s) must vote to skip `viên`."
        )
        status, message = self.mgr.vote_skip(CH, 2, "Ann")
        self.assertEqual(status, SkipStatus.SKIPPED)
        self.assertTrue(
            message.startswith(
                "⏭️ **Skipped!** Nobody could continue `viên`.\n> Could have continued with: `viên chức`\n\n"
                "🎮 **New round!**"
            ),
            message,
        )

    def test_09_stop(self):
        self.set_state()
        self.assertEqual(
            self.mgr.stop_game(CH),
            (True, "✅ **Word-chain game stopped**\n> Lasted **0** turn(s).\n> Could have continued with: `viên chức`"),
        )

    def test_10_status(self):
        self.set_state()
        self.assertEqual(
            self.mgr.game_status(CH),
            (
                True,
                "📋 **Word-chain status**\n"
                "> **Language:** `Vietnamese`\n"
                "> **Current phrase:** `sinh viên`\n"
                "> **Expected start word:** `viên`\n"
                "> **Turns:** `0`\n"
                "> **Per-user cooldown:** `5s`",
            ),
        )

    def test_11_check_word(self):
        self.set_state()
        self.assertEqual(
            self.mgr.check_word("sinh viên"),
            (True, "✅ **sinh viên** is in the dictionary. **1** word(s) can follow it."),
        )
        self.assertEqual(self.mgr.check_word("xyz abc"), (False, "❌ **xyz abc** is not in the dictionary."))
        # A single a-z token like "sinh" is now routed to the English check (by design), so the
        # unchanged Vietnamese shape message is checked with an accented single syllable.
        self.assertEqual(
            self.mgr.check_word("học"),
            (False, "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)"),
        )
        self.assertEqual(
            self.mgr.check_word("sinh", "vi"),
            (False, "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)"),
        )
        # 2-syllable words get the Vietnamese check even with no game or an English game.
        self.mgr._active_game = None
        self.assertEqual(
            self.mgr.check_word("sinh viên"),
            (True, "✅ **sinh viên** is in the dictionary. **1** word(s) can follow it."),
        )

    def test_12_add_remove(self):
        self.assertEqual(self.mgr.add_word("bàn ghế", 7), (True, "✅ Added **bàn ghế** to the dictionary."))
        self.assertEqual(
            self.mgr.add_word("bàn ghế", 7), (False, "ℹ️ **bàn ghế** is already in the dictionary.")
        )
        self.assertEqual(self.store.custom_words(), [("bàn ghế", True)])
        self.assertEqual(
            self.mgr.remove_word("bàn ghế", 7), (True, "🗑️ Removed **bàn ghế** from the dictionary.")
        )
        self.assertEqual(self.store.custom_words(), [("bàn ghế", False)])
        self.assertEqual(self.mgr.remove_word("xyz abc", 7), (False, "❌ This word is not in the dictionary."))
        self.assertEqual(
            self.mgr.add_word("ba con mèo", 7),
            (False, "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)"),
        )

    def test_13_saved_state_key_rebuilt(self):
        state = {
            "current_phrase": "hài hoà",
            "expected_start_word": "hoà",
            "language": "vi",
            "expected_start_key": "stale",
            "used": {key("hài hoà"): None},
            "last_player_id": None,
            "turns": 2,
        }
        self.store.save_game(CH, json.dumps(state, ensure_ascii=False))
        mgr = self.manager()
        self.assertEqual(mgr._active_game.expected_start_key, syllable_key("hòa"))
        self.assertEqual(mgr._active_game.turns, 2)
        self.assertEqual(mgr.handle_player_phrase(CH, 1, "Bob", "hòa bình").status, PhraseStatus.WIN)

    def test_custom_words_vi_only(self):
        self.store.set_custom_word("bàn ghế", True, 1)
        vi = make_vi()
        mgr = WordChainGameManager(store=self.store, vi_dictionary=vi, en_dictionary=make_en())
        mgr.apply_custom_words("vi")
        self.assertIn(key("bàn ghế"), vi)


class VietnameseRegressionViUi(ViBase):
    ui = "vi"

    def test_intro_and_correct(self):
        ok, message = self.mgr.start_game(CH)
        self.assertTrue(ok)
        self.assertEqual(
            message,
            "✅ **Trò chơi nối từ đã bắt đầu**\n"
            "> **Ngôn ngữ:** `Tiếng Việt`\n"
            "> **Từ bắt đầu:** `học sinh`\n"
            "> **Từ tiếp theo phải bắt đầu bằng:** `sinh`\n"
            "> **Luật:** đúng 2 âm tiết\n"
            "> **Cooldown mỗi người:** `5s`",
        )
        self.assertEqual(self.play("sinh viên").message, "✅ **Chính xác**\n> Từ tiếp theo phải bắt đầu bằng: `viên`")

    def test_errors(self):
        self.set_state()
        self.assertEqual(
            self.play("học sinh").message, "❌ **Sai từ bắt đầu**\n> Từ của bạn phải bắt đầu bằng: `viên`"
        )
        self.assertEqual(
            self.play("viên xyz").message, "❌ **Từ không tồn tại trong từ điển Tiếng Việt**\n> **viên xyz**"
        )

    def test_hint_skip_stop_status(self):
        self.set_state("lực", "nỗ lực")
        self.assertEqual(
            self.mgr.give_hint(CH, 1)[1],
            "💡 **Gợi ý:** `lực s_`\n> Có **1** từ có thể nối tiếp `lực`.\n> Lượt gợi ý còn lại hôm nay: **4/5**",
        )
        self.set_state()
        self.assertEqual(
            self.mgr.game_status(CH)[1],
            "📋 **Trạng thái nối từ**\n"
            "> **Ngôn ngữ:** `Tiếng Việt`\n"
            "> **Cụm từ hiện tại:** `sinh viên`\n"
            "> **Từ bắt đầu yêu cầu:** `viên`\n"
            "> **Số lượt nối:** `0`\n"
            "> **Cooldown mỗi người:** `5s`",
        )
        self.assertEqual(
            self.mgr.stop_game(CH)[1],
            "✅ **Đã dừng trò chơi nối từ**\n> Kéo dài **0** lượt nối.\n> Có thể nối bằng: `viên chức`",
        )
        self.set_state()
        self.mgr.vote_skip(CH, 1, "Bob")
        self.assertTrue(
            self.mgr.vote_skip(CH, 2, "Ann")[1].startswith(
                "⏭️ **Đã bỏ qua!** Không ai nối được `viên`.\n> Có thể nối bằng: `viên chức`"
            )
        )

    def test_add_word_messages(self):
        self.assertEqual(self.mgr.add_word("bàn ghế", 7), (True, "✅ Đã thêm **bàn ghế** vào từ điển."))
        self.assertEqual(self.mgr.add_word("ba con mèo", 7), (False, "❌ **Từ phải gồm đúng 2 âm tiết** (chỉ có chữ cái)"))
        self.assertEqual(self.mgr.remove_word("bàn ghế", 7), (True, "🗑️ Đã xoá **bàn ghế** khỏi từ điển."))


if __name__ == "__main__":
    unittest.main()
