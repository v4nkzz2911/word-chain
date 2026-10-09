"""Isolation tests: an English game must not affect the Vietnamese game, and vice versa.

The bot runs one game at a time but switches languages within the same process,
so these tests play one language, then the other, and check nothing leaks across.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage import GameStore  # noqa: E402
from vi_text import parse_word, syllable_key  # noqa: E402
from word_chain import PhraseStatus, SkipStatus, WordChainGameManager  # noqa: E402

from test_en_word_chain import CH, make_en, make_vi  # noqa: E402


def vi_key(display):
    return parse_word(display)[0]


def snapshot_vi(d):
    return dict(d.words), d.start_pool_size


def snapshot_en(d):
    return set(d.words), {k: set(v) for k, v in d.by_first.items() if v}, d.start_pool_size


class CrossLanguageCase(unittest.TestCase):
    ui = "en"

    def setUp(self) -> None:
        self.store = GameStore(":memory:")
        self.en = make_en()
        self.vi = make_vi()
        # The 4-word test list is too small for the real starter rules, so pin one starter.
        self.vi._start_pool = [k for k, display in self.vi.words.items() if display == "học sinh"]
        self.mgr = self.new_manager()

    def new_manager(self) -> WordChainGameManager:
        return WordChainGameManager(
            default_language="vi", ui_language=self.ui, store=self.store,
            vi_dictionary=self.vi, en_dictionary=self.en,
        )

    def play(self, text, user_id=1, name="Bob"):
        return self.mgr.handle_player_phrase(CH, user_id, name, text)

    def force_en(self, letter="e", phrase="apple"):
        """Start an English game, then pin a known state so the script is deterministic."""
        ok, _ = self.mgr.start_game(CH, "en")
        self.assertTrue(ok)
        g = self.mgr._active_game
        g.current_phrase, g.expected_start_word, g.expected_start_key = phrase, letter, letter
        g.used = {phrase: None}

    def force_vi(self, phrase="học sinh"):
        ok, _ = self.mgr.start_game(CH, "vi")
        self.assertTrue(ok)
        g = self.mgr._active_game
        last = phrase.split(" ")[-1]
        g.current_phrase, g.expected_start_word, g.expected_start_key = phrase, last, syllable_key(last)
        g.used = {vi_key(phrase): None}

    # ------------------------------------------------------------ switching games
    def test_english_game_leaves_nothing_for_vietnamese(self):
        vi_before = snapshot_vi(self.vi)
        self.force_en()
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)          # user 1 now on cooldown
        self.assertEqual(self.play("goat", 2, "Ann").status, PhraseStatus.OK)
        self.mgr.give_hint(CH, 3)
        self.assertEqual(self.mgr.vote_skip(CH, 3, "Cy")[0], SkipStatus.VOTED)  # one pending skip vote
        self.assertTrue(self.mgr.stop_game(CH)[0])

        self.force_vi()
        g = self.mgr._active_game
        self.assertEqual(g.language, "vi")
        self.assertEqual(g.turns, 0)
        self.assertEqual(set(g.used), {vi_key("học sinh")})                           # no English words carried over
        self.assertEqual(self.mgr._skip_votes, set())                         # English skip vote gone
        # user 1 answered in English a moment ago, but has no cooldown in the new Vietnamese game
        self.assertEqual(self.play("sinh viên").status, PhraseStatus.OK)
        self.assertEqual(snapshot_vi(self.vi), vi_before)

    def test_vietnamese_game_leaves_nothing_for_english(self):
        en_before = snapshot_en(self.en)
        self.force_vi()
        self.assertEqual(self.play("sinh viên").status, PhraseStatus.OK)
        self.mgr.give_hint(CH, 3)
        self.assertEqual(self.mgr.vote_skip(CH, 3, "Cy")[0], SkipStatus.VOTED)
        self.assertTrue(self.mgr.stop_game(CH)[0])

        self.force_en()
        g = self.mgr._active_game
        self.assertEqual((g.language, g.turns, set(g.used)), ("en", 0, {"apple"}))
        self.assertEqual(self.mgr._skip_votes, set())
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)            # no Vietnamese cooldown left
        self.assertEqual(snapshot_en(self.en), en_before)

    def test_cannot_start_other_language_while_one_runs(self):
        self.force_en()
        self.play("egg")
        before = self.mgr._active_game.to_json()
        ok, _ = self.mgr.start_game(CH, "vi")
        self.assertFalse(ok)
        self.assertEqual(self.mgr._active_game.to_json(), before)

        self.mgr.stop_game(CH)
        self.force_vi()
        self.play("sinh viên")
        before = self.mgr._active_game.to_json()
        ok, _ = self.mgr.start_game(CH, "en")
        self.assertFalse(ok)
        self.assertEqual(self.mgr._active_game.to_json(), before)

    # ------------------------------------------------------------ messages of the other language
    def test_vietnamese_messages_ignored_in_english_game(self):
        self.force_en()
        before = self.mgr._active_game.to_json()
        for text in ("sinh viên", "học sinh", "chào bạn nhé"):
            self.assertIsNone(self.play(text))
        self.assertEqual(self.mgr._active_game.to_json(), before)
        self.assertEqual(self.store.get_player(1)["wrong"], 0)

    def test_english_messages_ignored_in_vietnamese_game(self):
        self.force_vi()
        before = self.mgr._active_game.to_json()
        for text in ("egg", "apple", "**Egg!**", "tiger"):
            self.assertIsNone(self.play(text))
        self.assertEqual(self.mgr._active_game.to_json(), before)
        self.assertEqual(self.store.get_player(1)["wrong"], 0)

    # ------------------------------------------------------------ wins start a round in the same language
    def test_vietnamese_win_starts_vietnamese_round(self):
        self.force_vi()
        for i, word in enumerate(("sinh viên", "viên chức", "chức năng"), start=1):
            r = self.play(word, user_id=i)
        self.assertEqual(r.status, PhraseStatus.WIN)
        if self.mgr._active_game is not None:
            self.assertEqual(self.mgr._active_game.language, "vi")

    def test_english_win_starts_english_round(self):
        self.mgr._en_dictionary = self.en = make_en(["yak", "kiwi", "tiger", "rabbit", "train", "night"])
        self.force_en(letter="y", phrase="tiger")
        self.play("yak", 1)
        r = self.play("kiwi", 2)
        self.assertEqual(r.status, PhraseStatus.WIN)
        if self.mgr._active_game is not None:
            self.assertEqual(self.mgr._active_game.language, "en")

    # ------------------------------------------------------------ admin dictionary edits mid-game
    def test_admin_edits_during_english_game(self):
        self.force_en()
        game_before = self.mgr._active_game.to_json()
        en_before = snapshot_en(self.en)
        self.assertTrue(self.mgr.add_word("năng lực", 9)[0])                 # Vietnamese word
        self.assertTrue(self.mgr.remove_word("viên chức", 9)[0])
        self.assertEqual(snapshot_en(self.en), en_before)                    # English untouched
        self.assertEqual(self.mgr._active_game.to_json(), game_before)
        self.assertIn("năng lực", self.vi.words.values())
        self.assertNotIn("viên chức", self.vi.words.values())

    def test_admin_edits_during_vietnamese_game(self):
        self.force_vi()
        game_before = self.mgr._active_game.to_json()
        vi_before = snapshot_vi(self.vi)
        self.assertTrue(self.mgr.add_word("zyzzyva", 9)[0])                  # English word
        self.assertTrue(self.mgr.remove_word("tiger", 9)[0])
        self.assertEqual(snapshot_vi(self.vi), vi_before)                    # Vietnamese untouched
        self.assertEqual(self.mgr._active_game.to_json(), game_before)
        self.assertIn("zyzzyva", self.en)
        self.assertNotIn("tiger", self.en)

    def test_custom_words_replay_after_restart_stays_separate(self):
        self.mgr.add_word("năng lực", 9)
        self.mgr.add_word("zyzzyva", 9)
        self.mgr.remove_word("tiger", 9)
        self.mgr.remove_word("viên chức", 9)
        # restart: fresh dictionaries, replay each language like setup_hook does
        self.vi, self.en = make_vi(), make_en()
        self.mgr = self.new_manager()
        self.mgr.apply_custom_words("vi")
        self.assertEqual(snapshot_en(self.en), snapshot_en(make_en()))       # vi replay never touches en
        self.mgr.apply_custom_words("en")
        self.assertIn("năng lực", self.vi.words.values())
        self.assertNotIn("viên chức", self.vi.words.values())
        self.assertIn("zyzzyva", self.en)
        self.assertNotIn("tiger", self.en)
        self.assertNotIn("zyzzyva", self.vi.words.values())

    # ------------------------------------------------------------ restarts
    def test_restart_resumes_the_right_language(self):
        self.force_en()
        self.play("egg")
        self.mgr = self.new_manager()
        g = self.mgr._active_game
        self.assertEqual((g.language, g.expected_start_key), ("en", "g"))
        self.assertEqual(self.play("goat", 2).status, PhraseStatus.OK)
        self.mgr.stop_game(CH)

        self.force_vi()
        self.play("sinh viên")
        self.mgr = self.new_manager()
        g = self.mgr._active_game
        self.assertEqual((g.language, g.expected_start_key), ("vi", syllable_key("viên")))
        self.assertEqual(self.play("viên chức", 2).status, PhraseStatus.OK)

    # ------------------------------------------------------------ shared by design
    def test_stats_and_hint_limit_are_shared_by_design(self):
        self.force_en()
        self.play("egg")
        self.mgr.stop_game(CH)
        self.force_vi()
        self.play("sinh viên")
        self.assertEqual(self.store.get_player(1)["correct"], 2)


class CrossLanguageVietnameseUi(CrossLanguageCase):
    ui = "vi"


if __name__ == "__main__":
    unittest.main()
