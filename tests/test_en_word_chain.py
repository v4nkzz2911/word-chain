"""Tests for the English last-letter word-chain mode.

Run from the repo root: python -m unittest discover -s tests
No network access is needed. The test that uses the real ENABLE list is skipped unless
WORDCHAIN_ENABLE_PATH points to a downloaded enable1.txt / words_en_enable.txt.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from en_dictionary import EN_WORDLIST_FILE, EnglishDictionary, ensure_en_wordlist  # noqa: E402
from en_text import last_letter, mask_en_word, parse_en_word  # noqa: E402
from storage import GameStore  # noqa: E402
from vi_dictionary import VietnameseDictionary  # noqa: E402
from vi_text import parse_word  # noqa: E402
from word_chain import (  # noqa: E402
    VIETNAM_TZ,
    PhraseStatus,
    SkipStatus,
    WordChainGameManager,
    WordChainState,
)


CH = 100
EN_WORDS = [
    "apple", "egg", "goat", "tiger", "rabbit", "train", "night", "eagle", "elephant",
    "tree", "ear", "rug", "gum", "mug", "yak", "cafe", "zebra",
]
E_WORDS = {"egg", "eagle", "elephant", "ear"}
VI_WORDS = ["học sinh", "sinh viên", "viên chức", "chức năng"]


def make_en(words=EN_WORDS) -> EnglishDictionary:
    d = EnglishDictionary()
    d.load_lines(words)
    d.build_indexes()
    return d


def make_vi(words=VI_WORDS) -> VietnameseDictionary:
    d = VietnameseDictionary()
    d.load_lines(words)
    d.build_start_pool()
    return d


def today() -> str:
    return datetime.now(VIETNAM_TZ).date().isoformat()


class BaseCase(unittest.TestCase):
    ui = "en"

    def make_manager(self, store=None, en=None, vi=None, default_language="en") -> WordChainGameManager:
        return WordChainGameManager(
            default_language=default_language,
            ui_language=self.ui,
            store=store,
            vi_dictionary=vi if vi is not None else make_vi(),
            en_dictionary=en if en is not None else make_en(),
        )

    def setUp(self) -> None:
        self.store = GameStore(":memory:")
        self.en = make_en()
        self.vi = make_vi()
        self.mgr = self.make_manager(self.store, self.en, self.vi)

    def set_en_state(self, letter="e", phrase="apple", used=None) -> WordChainState:
        game = WordChainState(
            current_phrase=phrase,
            expected_start_word=letter,
            language="en",
            expected_start_key=letter,
            used=dict(used) if used is not None else {phrase: None},
        )
        self.mgr._active_channel_id = CH
        self.mgr._active_game = game
        self.mgr._last_answer_at.clear()
        self.mgr._skip_votes.clear()
        return game

    def set_vi_state(self, word="viên", phrase="sinh viên", used=None) -> WordChainState:
        from vi_text import syllable_key

        game = WordChainState(
            current_phrase=phrase,
            expected_start_word=word,
            language="vi",
            expected_start_key=syllable_key(word),
            used=dict(used) if used is not None else {parse_word(phrase)[0]: None},
        )
        self.mgr._active_channel_id = CH
        self.mgr._active_game = game
        self.mgr._last_answer_at.clear()
        self.mgr._skip_votes.clear()
        return game

    def play(self, text, user_id=1, name="Bob"):
        return self.mgr.handle_player_phrase(CH, user_id, name, text)


# ---------------------------------------------------------------- normalization
class NormalizationTests(unittest.TestCase):
    def test_valid_forms(self):
        cases = [
            "apple", "Apple", "APPLE", "apple!", "apple.", '"apple."', "(apple)",
            "**apple**", "__apple__", "||apple||", "`apple`", "~~apple~~", "*apple*",
            "ａｐｐｌｅ", "  apple  ",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_en_word(text), "apple")

    def test_fullwidth_and_unicode_trailing_punctuation(self):
        for text in ("ａｐｐｌｅ！", "apple‼", "apple…", "apple。", "**ａｐｐｌｅ**", "apple😀", "apple!?"):
            with self.subTest(text=text):
                self.assertEqual(parse_en_word(text), "apple")
        # Leading strip stays restrictive.
        for text in ("！apple", "‼apple", "😀apple", ":apple:", "@apple"):
            with self.subTest(text=text):
                self.assertIsNone(parse_en_word(text))

    def test_accents(self):
        self.assertEqual(parse_en_word("café"), "cafe")
        self.assertEqual(parse_en_word("CAFÉ!"), "cafe")
        self.assertEqual(parse_en_word("học"), "hoc")
        self.assertIsNone(parse_en_word("học", fold_accents=False))

    def test_invalid_forms(self):
        cases = [
            "apple pie", "> apple", "", "   ", "!apple", "/apple", "-apple", "<@123>",
            "https://x.com", "don't", "don’t", "x-ray", "abc1", "ok", "gg",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_en_word(text))

    def test_last_letter_and_mask(self):
        self.assertEqual(last_letter("king maker"), "r")
        self.assertIsNone(last_letter("123"))
        self.assertEqual(mask_en_word("apple"), "a____")


# ---------------------------------------------------------------- gameplay
class GameplayTests(BaseCase):
    def test_01_accept(self):
        self.set_en_state()
        result = self.play("egg")
        self.assertEqual(result.status, PhraseStatus.OK)
        self.assertIn("`g`", result.message)
        game = self.mgr._active_game
        self.assertEqual(game.expected_start_key, "g")
        self.assertEqual(game.expected_start_word, "g")
        self.assertEqual(game.turns, 1)
        self.assertEqual(game.used["egg"], "Bob")
        self.assertEqual(self.store.get_player(1, "en")["correct"], 1)
        saved = json.loads(self.store.load_game()[1])
        self.assertIn("egg", saved["used"])
        self.assertEqual(saved["expected_start_key"], "g")

    def test_02_cooldown(self):
        self.set_en_state()
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)
        result = self.play("goat")
        self.assertEqual(result.status, PhraseStatus.COOLDOWN)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)
        self.assertEqual(self.play("goat", user_id=2, name="Ann").status, PhraseStatus.OK)

    def test_03_wrong_start_is_chat(self):
        # One-word chat ("thanks", unaccented Vietnamese "roi") with the wrong first letter
        # is ignored: no reaction, no stat, no cooldown, the chain stays.
        self.set_en_state("g", "egg")
        for text in ("rabbit", "thanks", "roi", "Okay!"):
            with self.subTest(text=text):
                self.assertIsNone(self.play(text))
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 0, "wrong": 0, "wins": 0})
        self.assertNotIn((CH, 1), self.mgr._last_answer_at)
        self.assertEqual(self.mgr._active_game.expected_start_key, "g")
        self.assertEqual(self.mgr._active_game.turns, 0)

    def test_03b_wrong_start_ignored_during_cooldown(self):
        self.set_en_state()
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)
        self.assertIsNone(self.play("thanks"))  # chat, not ⏳
        self.assertEqual(self.play("goat").status, PhraseStatus.COOLDOWN)

    def test_04_not_in_dict(self):
        self.set_en_state("g", "egg")
        result = self.play("gxyzq")
        self.assertEqual(result.status, PhraseStatus.NOT_IN_DICT)
        self.assertIn("**gxyzq**", result.message)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 1)

    def test_05_used(self):
        self.set_en_state("e", "apple", {"apple": None, "egg": "Bob"})
        result = self.play("egg", user_id=2, name="Ann")
        self.assertEqual(result.status, PhraseStatus.USED)
        self.assertIn("Used by **Bob**", result.message)

        self.set_en_state("a", "zebra", {"apple": None, "zebra": "Ann"})
        result = self.play("apple")
        self.assertEqual(result.status, PhraseStatus.USED)
        self.assertIn("It was the starter word", result.message)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 1)

    def test_06_chat_ignored(self):
        self.set_en_state()
        for text in ("hello there", "ok", "!chainstatus", "don't", "egg 2"):
            with self.subTest(text=text):
                self.assertIsNone(self.play(text))
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 0, "wrong": 0, "wins": 0})
        self.assertEqual(self.store.get_player(1, "vi"), {"correct": 0, "wrong": 0, "wins": 0})
        self.assertEqual(self.mgr._active_game.turns, 0)

    def test_07_markdown_answer(self):
        self.set_en_state()
        result = self.play("**Egg!**")
        self.assertEqual(result.status, PhraseStatus.OK)
        self.assertEqual(self.mgr._active_game.current_phrase, "egg")
        self.assertIn("egg", self.mgr._active_game.used)

    def test_08_win_game_ends(self):
        en = make_en(["yak", "kiwi"])
        self.mgr = self.make_manager(self.store, en, self.vi)
        self.set_en_state("y", "toy", {"toy": None})
        self.assertEqual(self.play("yak").status, PhraseStatus.OK)
        self.assertEqual(self.mgr._active_game.expected_start_key, "k")
        result = self.play("kiwi", user_id=2, name="Ann")
        self.assertEqual(result.status, PhraseStatus.WIN)
        self.assertIn("🏆", result.message)
        self.assertIn("Ann wins with `kiwi`", result.message)
        self.assertEqual(self.store.get_player(2, "en")["wins"], 1)
        self.assertEqual(self.store.get_player(2, "en")["correct"], 1)
        # No starter word in this tiny dictionary: the game ends.
        self.assertFalse(self.mgr.has_game(CH))
        self.assertIsNone(self.store.load_game())

    def test_08b_win_new_round(self):
        en = make_en(EN_WORDS + ["kiwi"])
        self.mgr = self.make_manager(self.store, en, self.vi)
        self.set_en_state("k", "yak", {"yak": None})
        result = self.play("kiwi")
        self.assertEqual(result.status, PhraseStatus.WIN)
        self.assertIn("New round", result.message)
        self.assertTrue(self.mgr.has_game(CH))
        game = self.mgr._active_game
        self.assertEqual(game.turns, 0)
        self.assertEqual(len(game.expected_start_key), 1)
        self.assertIn(game.current_phrase, game.used)
        self.assertEqual(self.mgr._last_answer_at, {})

    def test_09_empty_dictionary_ignores(self):
        self.mgr = self.make_manager(self.store, EnglishDictionary(), self.vi)
        self.set_en_state()
        self.assertIsNone(self.play("egg"))

    def test_10_start_without_dictionary(self):
        self.mgr = self.make_manager(self.store, EnglishDictionary(), self.vi)
        ok, message = self.mgr.start_game(CH, "en")
        self.assertFalse(ok)
        self.assertIn("not available", message)
        self.assertFalse(self.mgr.has_game(CH))

    def test_10b_start_small_dictionary(self):
        ok, message = self.mgr.start_game(CH, "en")
        self.assertTrue(ok)
        game = self.mgr._active_game
        self.assertEqual(game.expected_start_key, game.current_phrase[-1])
        self.assertIn(game.current_phrase, game.used)
        self.assertIn("Next word must start with the letter", message)
        self.assertIn("one English word, 3+ letters", message)

    @unittest.skipUnless(os.environ.get("WORDCHAIN_ENABLE_PATH"), "set WORDCHAIN_ENABLE_PATH to the ENABLE file")
    def test_11_start_real_enable(self):
        en = EnglishDictionary()
        en.load_file(Path(os.environ["WORDCHAIN_ENABLE_PATH"]))
        self.assertGreater(len(en), 170_000)
        self.assertGreaterEqual(en.build_indexes(), 200)
        self.mgr = self.make_manager(self.store, en, self.vi)
        for _ in range(20):
            self.mgr._active_game = None
            ok, message = self.mgr.start_game(CH, "en")
            self.assertTrue(ok)
            game = self.mgr._active_game
            starter = game.current_phrase
            self.assertTrue(4 <= len(starter) <= 8, starter)
            self.assertNotIn(starter[-1], "jqxyz")
            self.assertIn(starter, game.used)
            self.assertEqual(len(game.expected_start_key), 1)
            self.assertIn("Next word must start with the letter", message)


# ---------------------------------------------------------------- persistence
class PersistenceTests(BaseCase):
    def reload(self, state: dict) -> WordChainGameManager:
        self.store.save_game(CH, json.dumps(state))
        return self.make_manager(self.store, self.en, self.vi)

    def test_12_old_phrase_state(self):
        self.mgr = self.reload({
            "current_phrase": "king maker",
            "expected_start_word": "maker",
            "language": "en",
            "expected_start_key": "maker",
            "used": {"king maker": "Bob"},
            "last_player_id": 1,
            "turns": 3,
        })
        game = self.mgr._active_game
        self.assertEqual(game.expected_start_word, "r")
        self.assertEqual(game.expected_start_key, "r")
        self.assertEqual(game.turns, 3)
        self.assertEqual(json.loads(self.store.load_game()[1])["expected_start_key"], "r")
        self.assertEqual(self.play("rabbit").status, PhraseStatus.OK)

    def test_13_old_state_without_key(self):
        self.mgr = self.reload({
            "current_phrase": "king Maker",
            "expected_start_word": "Maker",
            "language": "en",
            "used": {},
        })
        self.assertEqual(self.mgr._active_game.expected_start_key, "r")

    def test_14_unusable_state_dropped(self):
        self.mgr = self.reload({
            "current_phrase": "123",
            "expected_start_word": "123",
            "language": "en",
            "expected_start_key": "123",
            "used": {},
        })
        self.assertIsNone(self.store.load_game())
        self.assertFalse(self.mgr.has_game(CH))

    def test_15_new_state_round_trip(self):
        ok, _ = self.mgr.start_game(CH, "en")
        self.assertTrue(ok)
        letter = self.mgr._active_game.expected_start_key
        reloaded = self.make_manager(self.store, self.en, self.vi)
        self.assertTrue(reloaded.has_game(CH))
        self.assertEqual(reloaded._active_game.expected_start_key, letter)
        self.assertEqual(reloaded._active_game.expected_start_word, letter)


# ---------------------------------------------------------------- custom words
class CustomWordTests(BaseCase):
    def test_16_add_english(self):
        ok, message = self.mgr.add_word("Zyzzyvax", 9)
        self.assertTrue(ok)
        self.assertIn("Added **zyzzyvax** to the English dictionary", message)
        self.assertIn("zyzzyvax", self.en)
        self.assertIn(("zyzzyvax", True), self.store.custom_words())

    def test_17_add_existing(self):
        ok, message = self.mgr.add_word("apple", 9)
        self.assertFalse(ok)
        self.assertIn("already in the dictionary", message)

    def test_18_remove_english(self):
        ok, message = self.mgr.remove_word("apple", 9)
        self.assertTrue(ok)
        self.assertIn("Removed **apple** from the English dictionary", message)
        self.assertIn(("apple", False), self.store.custom_words())
        self.assertNotIn("apple", self.en)
        self.set_en_state("a", "zebra", {"zebra": None})
        self.assertEqual(self.play("apple").status, PhraseStatus.NOT_IN_DICT)
        ok, message = self.mgr.remove_word("apple", 9)
        self.assertFalse(ok)
        self.assertEqual(message, "❌ This word is not in the dictionary.")

    def test_19_add_vietnamese(self):
        self.assertEqual(
            self.mgr.add_word("học sinh", 9),
            (False, "ℹ️ **học sinh** is already in the dictionary."),
        )
        self.assertEqual(
            self.mgr.add_word("bàn ghế", 9),
            (True, "✅ Added **bàn ghế** to the dictionary."),
        )
        self.assertIn(parse_word("bàn ghế")[0], self.vi)
        self.assertIn(("bàn ghế", True), self.store.custom_words())

    def test_20_single_accented_token(self):
        en_size, vi_size = len(self.en), len(self.vi)
        ok, message = self.mgr.add_word("học", 9)
        self.assertFalse(ok)
        self.assertIn("Invalid word", message)
        self.assertEqual((len(self.en), len(self.vi)), (en_size, vi_size))
        self.assertEqual(self.store.custom_words(), [])

    def test_21_three_syllables(self):
        self.assertEqual(
            self.mgr.add_word("ba con mèo", 9),
            (False, "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)"),
        )

    def test_22_apply_custom_words_routing(self):
        self.store.set_custom_word("zyzzyvax", True, 1)
        self.store.set_custom_word("apple", False, 1)
        self.store.set_custom_word("học sinh", True, 1)
        vi_key = parse_word("học sinh")[0]

        def fresh():
            en, vi = make_en(), make_vi(["sinh viên"])
            return en, vi, self.make_manager(self.store, en, vi)

        en, vi, mgr = fresh()
        mgr.apply_custom_words("en")
        self.assertIn("zyzzyvax", en)
        self.assertNotIn("apple", en)
        self.assertNotIn(vi_key, vi)

        en, vi, mgr = fresh()
        mgr.apply_custom_words("vi")
        self.assertNotIn("zyzzyvax", en)
        self.assertIn("apple", en)
        self.assertIn(vi_key, vi)

        en, vi, mgr = fresh()
        mgr.apply_custom_words()
        self.assertIn("zyzzyvax", en)
        self.assertNotIn("apple", en)
        self.assertIn(vi_key, vi)

    def test_23_ascii_token_is_english(self):
        ok, message = self.mgr.add_word("hoa", 9)
        self.assertTrue(ok)
        self.assertIn("English dictionary", message)
        self.assertIn("hoa", self.en)


# ---------------------------------------------------------------- hints / check / skip / stop
class ToolTests(BaseCase):
    def test_24_hint(self):
        self.set_en_state()
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertTrue(ok)
        self.assertRegex(message, r"💡 \*\*Hint:\*\* `e_+`")
        self.assertIn(f"**{len(E_WORDS)}** word(s) can follow (starting with `e`)", message)
        self.assertIn("Hints left today: **4/5**", message)
        self.assertEqual(self.store.hints_used(1, today()), 1)

    def test_25_shared_hint_counter(self):
        self.set_en_state()
        for _ in range(3):
            self.assertTrue(self.mgr.give_hint(CH, 1)[0])
        self.set_vi_state()
        for _ in range(2):
            self.assertTrue(self.mgr.give_hint(CH, 1)[0])
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertFalse(ok)
        self.assertIn("used all 5 hints", message)

    def test_26_hint_no_continuation(self):
        self.set_en_state("i", "taxi", {"taxi": None})
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertFalse(ok)
        self.assertIn("No word can follow anymore", message)
        self.assertEqual(self.store.hints_used(1, today()), 0)

    def test_27_example_prefers_common(self):
        en = make_en()
        en._common_by_first.clear()
        en._common_by_first["e"] = {"egg"}
        for _ in range(30):
            self.assertEqual(en.example("e", {"apple"}), "egg")
        self.assertIn(en.example("e", {"apple", "egg"}), E_WORDS - {"egg"})
        self.assertIsNone(en.example("i"))

    def test_28b_check_word_routing(self):
        # One a-z token -> English, even during a Vietnamese game.
        self.set_vi_state()
        ok, message = self.mgr.check_word("apple")
        self.assertTrue(ok)
        self.assertIn("can follow it (starting with `e`)", message)
        # Several tokens -> Vietnamese, even during an English game (vi text unchanged).
        self.set_en_state()
        self.assertEqual(
            self.mgr.check_word("sinh viên"),
            (True, "✅ **sinh viên** is in the dictionary. **1** word(s) can follow it."),
        )
        self.assertEqual(self.mgr.check_word("xyz abc"), (False, "❌ **xyz abc** is not in the dictionary."))
        # Single non a-z token -> language of the active game.
        self.assertIn("one word of 3+ letters", self.mgr.check_word("ok")[1])
        ok, message = self.mgr.check_word("Café")  # accented: not strict a-z, English game -> folded
        self.assertTrue(ok)
        self.assertIn("**cafe**", message)
        self.set_vi_state()
        self.assertEqual(
            self.mgr.check_word("học"),
            (False, "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)"),
        )
        # No game: default language (en), which folds accents like in play.
        self.mgr._active_game = None
        self.assertEqual(self.mgr.check_word("học"), (False, "❌ **hoc** is not in the dictionary."))
        # An explicit language is respected.
        self.assertIn("one word of 3+ letters", self.mgr.check_word("apple pie", "en")[1])
        self.assertIn("exactly 2 syllables", self.mgr.check_word("apple", "vi")[1])

    def test_28_check_word(self):
        ok, message = self.mgr.check_word("apple", "en")
        self.assertTrue(ok)
        self.assertIn(f"**{len(E_WORDS)}** word(s) can follow it (starting with `e`)", message)
        ok, message = self.mgr.check_word("apple pie", "en")
        self.assertFalse(ok)
        self.assertIn("one word of 3+ letters", message)
        ok, message = self.mgr.check_word("qqqq", "en")
        self.assertFalse(ok)
        self.assertIn("is not in the dictionary", message)
        # Language taken from the active English game.
        self.set_en_state()
        self.assertTrue(self.mgr.check_word("Egg")[0])

    def test_29_vote_skip(self):
        old = self.set_en_state()
        self.mgr._last_answer_at[(CH, 5)] = 0.0
        status, _ = self.mgr.vote_skip(CH, 1, "Bob")
        self.assertEqual(status, SkipStatus.VOTED)
        status, message = self.mgr.vote_skip(CH, 2, "Ann")
        self.assertEqual(status, SkipStatus.SKIPPED)
        self.assertIn("Nobody found a word starting with `e`", message)
        match = re.search(r"Could have continued with: `(\w+)`", message)
        self.assertIsNotNone(match)
        self.assertIn(match.group(1), E_WORDS)
        self.assertIn("New round", message)
        self.assertIsNot(self.mgr._active_game, old)
        self.assertEqual(self.mgr._active_game.language, "en")
        self.assertEqual(self.mgr._skip_votes, set())
        self.assertEqual(self.mgr._last_answer_at, {})

    def test_30_stop(self):
        self.set_en_state()
        ok, message = self.mgr.stop_game(CH)
        self.assertTrue(ok)
        self.assertIn("Could have continued with: `e", message)
        self.assertFalse(self.mgr.has_game(CH))

    def test_31_status(self):
        self.set_en_state()
        ok, message = self.mgr.game_status(CH)
        self.assertTrue(ok)
        self.assertIn("**Current word:** `apple`", message)
        self.assertIn("**Next word must start with the letter:** `e`", message)


class VietnameseUiToolTests(BaseCase):
    """32. Same English-game tools with the Vietnamese bot language."""

    ui = "vi"

    def test_hint(self):
        self.set_en_state()
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertTrue(ok)
        self.assertRegex(message, r"💡 \*\*Gợi ý:\*\* `e_+`")
        self.assertIn(f"Có **{len(E_WORDS)}** từ có thể nối tiếp (bắt đầu bằng chữ `e`)", message)

    def test_check(self):
        ok, message = self.mgr.check_word("apple", "en")
        self.assertTrue(ok)
        self.assertIn("có trong từ điển", message)
        self.assertIn("bắt đầu bằng chữ `e`", message)
        self.assertIn("Từ tiếng Anh phải là một từ", self.mgr.check_word("apple pie", "en")[1])
        self.assertIn("không có trong từ điển", self.mgr.check_word("qqqq", "en")[1])

    def test_skip(self):
        self.set_en_state()
        self.mgr.vote_skip(CH, 1, "Bob")
        status, message = self.mgr.vote_skip(CH, 2, "Ann")
        self.assertEqual(status, SkipStatus.SKIPPED)
        self.assertIn("Không ai tìm được từ bắt đầu bằng chữ `e`", message)
        self.assertIn("Có thể nối bằng: `e", message)

    def test_stop(self):
        self.set_en_state()
        self.assertIn("Có thể nối bằng: `e", self.mgr.stop_game(CH)[1])

    def test_status(self):
        self.set_en_state()
        message = self.mgr.game_status(CH)[1]
        self.assertIn("**Từ hiện tại:** `apple`", message)
        self.assertIn("**Từ tiếp theo phải bắt đầu bằng chữ:** `e`", message)

    def test_play_messages(self):
        self.set_en_state()
        self.assertIsNone(self.play("goat"))  # wrong first letter = chat
        self.assertIn("Từ không tồn tại trong từ điển Tiếng Anh", self.play("eqqq").message)
        self.assertIn("Từ tiếp theo phải bắt đầu bằng chữ: `g`", self.play("egg").message)

    def test_add_remove(self):
        self.assertEqual(
            self.mgr.add_word("zyzzyvax", 1), (True, "✅ Đã thêm **zyzzyvax** vào từ điển tiếng Anh.")
        )
        self.assertEqual(
            self.mgr.remove_word("zyzzyvax", 1), (True, "🗑️ Đã xoá **zyzzyvax** khỏi từ điển tiếng Anh.")
        )
        self.assertIn("Từ không hợp lệ", self.mgr.add_word("học", 1)[1])

    def test_unavailable(self):
        self.mgr = self.make_manager(self.store, EnglishDictionary(), self.vi)
        ok, message = self.mgr.start_game(CH, "en")
        self.assertFalse(ok)
        self.assertIn("Từ điển tiếng Anh chưa sẵn sàng", message)


# ---------------------------------------------------------------- offensive words
OFFENSIVE_SAMPLE = [
    "fuck", "fucking", "shit", "shitty", "bitch", "bitches", "cunt", "slut", "whore", "dick",
    "cock", "bastard", "retard", "retarded", "rape", "raped", "raping", "porn", "boobs", "penis",
    "anal", "nazi", "nazis", "niggers", "faggot", "fag", "dyke", "pussy", "tits", "twat",
]
CLEAN_LOOKALIKES = [
    "dictionary", "cocktail", "analyze", "rapeseed", "title", "pimple", "spicy", "scunthorpe",
    "class", "assess", "therapist", "grape", "shiitake", "spiced", "spicing", "japan",
]


class OffensiveWordTests(BaseCase):
    def test_is_offensive(self):
        from en_dictionary import is_offensive

        for word in OFFENSIVE_SAMPLE:
            with self.subTest(word=word):
                self.assertTrue(is_offensive(word))
        for word in CLEAN_LOOKALIKES:
            with self.subTest(word=word):
                self.assertFalse(is_offensive(word))

    def test_never_starter_or_common(self):
        words = EN_WORDS + OFFENSIVE_SAMPLE + ["texas", "china", "trump", "carl", "lewis", "tree"]
        en = make_en(words)
        pool = set(en._start_pool)
        common = set().union(*en._common_by_first.values())
        for word in OFFENSIVE_SAMPLE:
            self.assertNotIn(word, pool)
            self.assertNotIn(word, common)
        for word in ("texas", "china", "trump", "carl", "lewis"):
            self.assertNotIn(word, pool)
        self.assertIn("apple", pool)
        # add() after indexing does not put offensive words into the common index either.
        en.add("shithead")
        self.assertNotIn("shithead", en._common_by_first["s"])
        self.assertIn("shithead", en)

    def test_example_skips_offensive(self):
        en = make_en(["sun", "shit", "slut", "shitty"])
        en._common_by_first["s"] |= {"shit", "slut"}  # even if they slipped into the common index
        for _ in range(30):
            self.assertEqual(en.example("s", ()), "sun")
        self.assertIsNone(en.example("s", {"sun"}))

    def test_hint_and_skip_never_show_offensive(self):
        en = make_en(["apple", "fuck", "fucking", "frog"])
        self.mgr = self.make_manager(self.store, en, self.vi)
        self.set_en_state("f", "leaf", {"leaf": None})
        for user in range(1, 4):
            ok, message = self.mgr.give_hint(CH, user)
            self.assertTrue(ok)
            self.assertIn("`f___`", message)  # frog
        self.set_en_state("f", "leaf", {"leaf": None, "frog": "Bob"})
        ok, message = self.mgr.give_hint(CH, 9)
        self.assertFalse(ok)
        self.assertIn("No word can follow anymore", message)
        self.assertEqual(self.store.hints_used(9, today()), 0)
        ok, message = self.mgr.stop_game(CH)
        self.assertNotIn("Could have continued", message)

    def test_players_cannot_play_them(self):
        en = make_en(EN_WORDS + ["shit"])
        self.mgr = self.make_manager(self.store, en, self.vi)
        self.set_en_state("s", "bus", {"bus": None})
        result = self.play("shit")
        self.assertEqual(result.status, PhraseStatus.BANNED)
        self.assertFalse(result.accepted)
        self.assertNotIn("shit", self.mgr._active_game.used)


BANNED_WARNING_EN = (
    "⚠️ **Warning, Bob: that word is not allowed**\n"
    "> Offensive words are banned in this game. Please keep the chat friendly."
)
BANNED_WARNING_VI = (
    "⚠️ **Cảnh báo Bob: từ này không được phép dùng**\n"
    "> Từ ngữ xúc phạm bị cấm trong trò chơi. Hãy giữ không khí thân thiện nhé."
)
COLLATERAL_BANNED = [
    "fucking", "shitty", "niggle", "pussycat", "bitches", "raped", "raping", "cocked", "dyked",
    "craps", "asses", "sexes", "hell", "hells", "squaw", "jewed",
]
# "jew"/"jews" are neutral words for a people, not slurs: they must stay playable.
NOT_BANNED = ["cocktail", "dictionary", "hello", "spiced", "title", "jew", "jews"]


class OffensiveGameplayTests(BaseCase):
    """Offensive words are refused in English games with a warning; nothing else changes."""

    def setUp(self) -> None:
        super().setUp()
        self.en = make_en(EN_WORDS + ["shit", "dick", "hell", "cocktail", "dictionary", "hello", "spiced", "gook"])
        self.mgr = self.make_manager(self.store, self.en, self.vi)
        self.set_en_state("e", "apple")

    def warning(self) -> str:
        return BANNED_WARNING_VI if self.ui == "vi" else BANNED_WARNING_EN

    def test_banned_message(self):
        result = self.play("**Fuck!**")
        self.assertEqual(result.status, PhraseStatus.BANNED)
        self.assertEqual(result.message, self.warning())
        self.assertNotIn("fuck", result.message.lower())
        self.assertFalse(result.accepted)
        self.assertEqual(self.play("Fück").status, PhraseStatus.BANNED)

    def test_banned_before_wrong_start(self):
        self.assertEqual(self.play("shit").status, PhraseStatus.BANNED)  # letter is e

    def test_banned_before_not_in_dict(self):
        self.assertNotIn("fucking", self.en)
        self.assertEqual(self.play("fucking").status, PhraseStatus.BANNED)

    def test_banned_before_used(self):
        self.set_en_state("d", "bed", {"bed": None, "dick": "Ann"})
        self.assertEqual(self.play("dick").status, PhraseStatus.BANNED)

    def test_banned_before_cooldown_and_cooldown_untouched(self):
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)
        stamp = self.mgr._last_answer_at[(CH, 1)]
        self.assertEqual(self.play("gook").status, PhraseStatus.BANNED)
        self.assertEqual(self.mgr._last_answer_at[(CH, 1)], stamp)
        self.assertEqual(self.play("goat").status, PhraseStatus.COOLDOWN)

    def test_banned_word_forms(self):
        for word in COLLATERAL_BANNED:
            with self.subTest(word=word):
                self.set_en_state(word[0], "x" + word[0], {"x" + word[0]: None})
                self.assertEqual(self.play(word).status, PhraseStatus.BANNED)
        for word in NOT_BANNED:
            with self.subTest(word=word):
                self.set_en_state(word[0], "x" + word[0], {"x" + word[0]: None})
                self.mgr._last_answer_at.clear()
                self.assertNotEqual(self.play(word).status, PhraseStatus.BANNED)

    def test_counts_as_wrong(self):
        self.play("shit")
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 0, "wrong": 1, "wins": 0})
        self.assertEqual(self.store.get_player(1, "vi"), {"correct": 0, "wrong": 0, "wins": 0})

    def test_state_unchanged(self):
        self.assertEqual(self.mgr.vote_skip(CH, 3, "Cy")[0], SkipStatus.VOTED)
        game = self.mgr._active_game
        before_json = game.to_json()
        before_saved = self.store.load_game()
        self.assertEqual(self.play("hell").status, PhraseStatus.BANNED)
        self.assertIs(self.mgr._active_game, game)
        self.assertEqual(game.to_json(), before_json)
        self.assertEqual(game.turns, 0)
        self.assertEqual(game.used, {"apple": None})
        self.assertEqual(game.current_phrase, "apple")
        self.assertIsNone(game.last_player_id)
        self.assertEqual(self.store.load_game(), before_saved)
        self.assertEqual(self.mgr._skip_votes, {3})
        self.assertEqual(self.mgr._last_answer_at, {})

    def test_can_answer_right_after(self):
        self.assertEqual(self.play("shit").status, PhraseStatus.BANNED)
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 1, "wrong": 1, "wins": 0})

    def test_warning_names_the_player(self):
        message = self.play("shit", 2, "Ann").message
        self.assertIn("Ann", message)
        if self.ui == "en":
            self.assertIn("Warning, Ann:", message)
        else:
            self.assertIn("Cảnh báo Ann:", message)

    def test_chat_with_offensive_word_ignored(self):
        self.assertIsNone(self.play("fuck you"))
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)

    # ---------- /chaincheck
    def test_check_word_banned(self):
        if self.ui == "vi":
            self.assertEqual(self.mgr.check_word("shit"), (False, "🚫 **shit** bị cấm trong trò chơi."))
            return
        self.assertEqual(self.mgr.check_word("fuck"), (False, "🚫 **fuck** is banned in games."))
        self.assertEqual(self.mgr.check_word("Shit!"), (False, "🚫 **shit** is banned in games."))
        self.assertEqual(self.mgr.check_word("shit", "en"), (False, "🚫 **shit** is banned in games."))

    def test_check_word_during_vietnamese_game(self):
        self.set_vi_state()
        expected = "🚫 **hell** bị cấm trong trò chơi." if self.ui == "vi" else "🚫 **hell** is banned in games."
        self.assertEqual(self.mgr.check_word("hell"), (False, expected))
        self.assertIn(
            "2 âm tiết" if self.ui == "vi" else "exactly 2 syllables", self.mgr.check_word("hell", "vi")[1]
        )

    def test_check_word_clean_and_unavailable(self):
        ok, message = self.mgr.check_word("cocktail")
        self.assertTrue(ok)
        self.assertIn("**cocktail**", message)
        self.mgr = self.make_manager(self.store, EnglishDictionary(), self.vi)
        self.assertEqual(self.mgr.check_word("fuck"), (False, self.mgr._en_unavailable_message()))

    # ---------- /chainadd, /chainremove
    def test_add_banned_word_refused(self):
        expected = (
            "❌ **fuckwit** nằm trong danh sách từ cấm nên không thể thêm."
            if self.ui == "vi"
            else "❌ **fuckwit** is on the banned word list and can't be added."
        )
        self.assertEqual(self.mgr.add_word("fuckwit", 9), (False, expected))
        self.assertNotIn("fuckwit", self.en)
        self.assertEqual(self.store.custom_words(), [])

    def test_add_banned_word_already_in_dictionary(self):
        if self.ui == "vi":
            return
        self.assertEqual(
            self.mgr.add_word("shit", 9), (False, "❌ **shit** is on the banned word list and can't be added.")
        )
        self.assertEqual(
            self.mgr.add_word("HELL", 9), (False, "❌ **hell** is on the banned word list and can't be added.")
        )
        self.assertEqual(self.store.custom_words(), [])

    def test_remove_banned_word_allowed(self):
        if self.ui == "vi":
            self.assertEqual(self.mgr.remove_word("shit", 9), (True, "🗑️ Đã xoá **shit** khỏi từ điển tiếng Anh."))
        else:
            self.assertEqual(
                self.mgr.remove_word("shit", 9), (True, "🗑️ Removed **shit** from the English dictionary.")
            )
        self.assertIn(("shit", False), self.store.custom_words())
        self.assertNotIn("shit", self.en)

    def test_add_other_words_unchanged(self):
        if self.ui == "vi":
            self.assertEqual(
                self.mgr.add_word("zyzzyvax", 9), (True, "✅ Đã thêm **zyzzyvax** vào từ điển tiếng Anh.")
            )
            self.assertEqual(self.mgr.add_word("bàn ghế", 9), (True, "✅ Đã thêm **bàn ghế** vào từ điển."))
        else:
            self.assertEqual(
                self.mgr.add_word("zyzzyvax", 9), (True, "✅ Added **zyzzyvax** to the English dictionary.")
            )
            self.assertEqual(self.mgr.add_word("bàn ghế", 9), (True, "✅ Added **bàn ghế** to the dictionary."))


class OffensiveGameplayViUiTests(OffensiveGameplayTests):
    ui = "vi"


ENABLE_COLLATERAL = [
    "hell", "hells", "cocked", "craps", "niggle", "niggling", "pussycat", "dyked", "asses", "sexes",
    "squaw", "jewed", "spics", "bastardy", "pricked", "pricking", "pussyfoot", "niggard", "shittah",
    "shittim", "shitake", "twattle", "negroni", "pissoir", "fagot", "cocking", "craped",
]
ENABLE_LOOKALIKES = [
    "spice", "spiced", "japan", "hello", "shell", "cocktail", "dictionary", "analyze", "rapeseed",
    "grape", "title", "assess", "class", "therapist", "shiitake", "peninsula", "prickly", "squawk", "jew", "jews", "jewel",
]


class OffensiveEnableTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("WORDCHAIN_ENABLE_PATH"), "set WORDCHAIN_ENABLE_PATH to the ENABLE file")
    def test_collateral_and_lookalikes_in_enable(self):
        from en_dictionary import is_offensive

        en = EnglishDictionary()
        en.load_file(Path(os.environ["WORDCHAIN_ENABLE_PATH"]))
        for word in ENABLE_COLLATERAL:
            with self.subTest(word=word):
                self.assertIn(word, en)
                self.assertTrue(is_offensive(word))
        for word in ENABLE_LOOKALIKES:
            with self.subTest(word=word):
                self.assertFalse(is_offensive(word))


class NotShownByBotTests(unittest.TestCase):
    """'jew'/'jews' are playable but the bot never picks them as starters, hints or answers."""

    def test_playable_but_never_picked(self):
        en = make_en(["jew", "jews", "jewel", "jeweler", "egg", "seed", "tiger", "rabbit"])
        self.assertEqual(en.start_pool_size, len(en._start_pool))
        self.assertNotIn("jew", en._start_pool)
        self.assertNotIn("jews", en._start_pool)
        for _ in range(200):
            self.assertIn(en.example("j"), {"jewel", "jeweler"})
        self.assertIsNone(en.example("j", {"jewel", "jeweler"}))

        mgr = WordChainGameManager(store=GameStore(":memory:"), en_dictionary=en, vi_dictionary=make_vi())
        mgr._active_channel_id = CH
        mgr._active_game = WordChainState(
            current_phrase="egg", expected_start_word="j", language="en",
            expected_start_key="j", used={"egg": None},
        )
        self.assertEqual(mgr.handle_player_phrase(CH, 1, "Bob", "jews").status, PhraseStatus.OK)

    @unittest.skipUnless(os.environ.get("WORDCHAIN_ENABLE_PATH"), "set WORDCHAIN_ENABLE_PATH to the ENABLE file")
    def test_real_enable_never_picks_them(self):
        en = EnglishDictionary()
        en.load_file(Path(os.environ["WORDCHAIN_ENABLE_PATH"]))
        en.build_indexes()
        for word in ("jew", "jews"):
            self.assertIn(word, en)
            self.assertNotIn(word, en._start_pool)
            self.assertNotIn(word, en._common_by_first["j"])


# ---------------------------------------------------------------- loading
class LoadingTests(unittest.TestCase):
    def test_33_download_failure_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / EN_WORDLIST_FILE
            with self.assertRaises(OSError):
                ensure_en_wordlist(path, "http://127.0.0.1:9/x")
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_35_incomplete_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / EN_WORDLIST_FILE).write_text("apple\negg\ngoat\ntiger\nrabbit\n", encoding="utf-8")
            mgr = WordChainGameManager(en_dictionary=EnglishDictionary(), vi_dictionary=make_vi())
            with self.assertLogs("hqs-bot", "WARNING"):
                with self.assertRaisesRegex(ValueError, "looks incomplete"):
                    mgr.load_english_word_files(data_dir)
            # Fix 1: nothing from the broken file is kept, English stays unavailable.
            self.assertEqual(len(mgr.en_dictionary), 0)
            ok, message = mgr.start_game(CH, "en")
            self.assertFalse(ok)
            self.assertIn("not available", message)
            self.assertFalse(mgr.has_game(CH))
            # The broken file is moved away (name still matched by data/words*.txt in .gitignore),
            # so the next start downloads the list again.
            self.assertFalse((data_dir / EN_WORDLIST_FILE).exists())
            self.assertTrue((data_dir / "words_en_enable.bad.txt").exists())

    def test_35b_failed_load_keeps_previous_dictionary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / EN_WORDLIST_FILE).write_text("zebra\nzoo\n", encoding="utf-8")
            en = make_en()
            mgr = WordChainGameManager(en_dictionary=en, vi_dictionary=make_vi())
            with self.assertLogs("hqs-bot", "WARNING"):
                with self.assertRaises(ValueError):
                    mgr.load_english_word_files(data_dir)
            self.assertIs(mgr.en_dictionary, en)
            self.assertNotIn("zoo", en)

    def test_download_html_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text("  <!DOCTYPE html><html><body>Not found</body></html>", encoding="utf-8")
            path = Path(tmp) / "data" / EN_WORDLIST_FILE
            with self.assertRaisesRegex(OSError, "HTML"):
                ensure_en_wordlist(path, page.as_uri())
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_download_streams_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "list.txt"
            lines = [f"word{chr(97 + i % 26)}{i}" for i in range(5000)]  # > one 4 KB chunk
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            path = Path(tmp) / "data" / EN_WORDLIST_FILE
            self.assertEqual(ensure_en_wordlist(path, source.as_uri()), path)
            self.assertEqual(path.read_bytes(), source.read_bytes())
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_download_uses_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / EN_WORDLIST_FILE
            with mock.patch("en_dictionary.urllib.request.urlopen", side_effect=TimeoutError("slow")) as urlopen:
                with self.assertRaises(OSError):
                    ensure_en_wordlist(path, "https://example.invalid/list.txt")
            self.assertEqual(urlopen.call_args.kwargs.get("timeout"), 60)
            self.assertFalse(path.exists())

    def test_load_with_extra_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / EN_WORDLIST_FILE).write_text("aa\napple\negg\n", encoding="utf-8")
            (data_dir / "extra_words_en.txt").write_text("zyzzyvax\n", encoding="utf-8")
            mgr = WordChainGameManager(en_dictionary=EnglishDictionary(["egg"]), vi_dictionary=make_vi())
            with mock.patch("word_chain.MIN_EXPECTED_WORDS", 1):
                self.assertEqual(mgr.load_english_word_files(data_dir), 3)
            en = mgr.en_dictionary
            self.assertNotIn("aa", en)  # 1-2 letter words are dropped
            self.assertIn("zyzzyvax", en)
            self.assertEqual(en.starter_blacklist, frozenset({"egg"}))  # blacklist carried over

    @unittest.skipUnless(os.environ.get("WORDCHAIN_ENABLE_PATH"), "set WORDCHAIN_ENABLE_PATH to the ENABLE file")
    def test_real_enable_every_letter_playable(self):
        en = EnglishDictionary()
        en.load_file(Path(os.environ["WORDCHAIN_ENABLE_PATH"]))
        for letter in "abcdefghijklmnopqrstuvwxyz":
            self.assertGreater(en.count_continuations(letter), 0, letter)
        en.build_indexes()
        pool = set(en._start_pool)
        common = set().union(*en._common_by_first.values())
        for word in OFFENSIVE_SAMPLE + ["texas", "china", "trump", "carl", "lewis"]:
            with self.subTest(word=word):
                self.assertNotIn(word, pool)
                if word in OFFENSIVE_SAMPLE:
                    self.assertNotIn(word, common)


if __name__ == "__main__":
    unittest.main()
