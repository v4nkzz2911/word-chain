"""Isolation tests: an English game must not affect the Vietnamese game, and vice versa.

A channel runs one game at a time but can switch languages within the same process,
so these tests play one language, then the other, in one channel and check nothing leaks across.
Games running in several channels at once are covered by test_multi_channel.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage import GameStore  # noqa: E402
from vi_text import parse_word, syllable_key  # noqa: E402
from word_chain import PhraseStatus, SkipStatus, WordChainGameManager  # noqa: E402

from test_en_word_chain import CH, make_en, make_vi, today  # noqa: E402


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
        g = self.mgr.game_for(CH)
        g.current_phrase, g.expected_start_word, g.expected_start_key = phrase, letter, letter
        g.used = {phrase: None}

    def force_vi(self, phrase="học sinh"):
        ok, _ = self.mgr.start_game(CH, "vi")
        self.assertTrue(ok)
        g = self.mgr.game_for(CH)
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
        g = self.mgr.game_for(CH)
        self.assertEqual(g.language, "vi")
        self.assertEqual(g.turns, 0)
        self.assertEqual(set(g.used), {vi_key("học sinh")})                           # no English words carried over
        self.assertEqual(self.mgr._sessions[CH].skip_votes, set())                         # English skip vote gone
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
        g = self.mgr.game_for(CH)
        self.assertEqual((g.language, g.turns, set(g.used)), ("en", 0, {"apple"}))
        self.assertEqual(self.mgr._sessions[CH].skip_votes, set())
        self.assertEqual(self.play("egg").status, PhraseStatus.OK)            # no Vietnamese cooldown left
        self.assertEqual(snapshot_en(self.en), en_before)

    def test_cannot_start_other_language_while_one_runs(self):
        self.force_en()
        self.play("egg")
        before = self.mgr.game_for(CH).to_json()
        ok, _ = self.mgr.start_game(CH, "vi")
        self.assertFalse(ok)
        self.assertEqual(self.mgr.game_for(CH).to_json(), before)

        self.mgr.stop_game(CH)
        self.force_vi()
        self.play("sinh viên")
        before = self.mgr.game_for(CH).to_json()
        ok, _ = self.mgr.start_game(CH, "en")
        self.assertFalse(ok)
        self.assertEqual(self.mgr.game_for(CH).to_json(), before)

    # ------------------------------------------------------------ messages of the other language
    def test_vietnamese_messages_ignored_in_english_game(self):
        self.force_en()
        before = self.mgr.game_for(CH).to_json()
        for text in ("sinh viên", "học sinh", "chào bạn nhé"):
            self.assertIsNone(self.play(text))
        self.assertEqual(self.mgr.game_for(CH).to_json(), before)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)
        self.assertEqual(self.store.get_player(1, "vi")["wrong"], 0)

    def test_english_messages_ignored_in_vietnamese_game(self):
        self.force_vi()
        before = self.mgr.game_for(CH).to_json()
        for text in ("egg", "apple", "**Egg!**", "tiger"):
            self.assertIsNone(self.play(text))
        self.assertEqual(self.mgr.game_for(CH).to_json(), before)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)
        self.assertEqual(self.store.get_player(1, "vi")["wrong"], 0)

    def test_offensive_english_words_ignored_in_vietnamese_game(self):
        self.mgr._en_dictionary = self.en = make_en(
            ["apple", "egg", "goat", "tiger", "shit", "hell", "dick"]
        )
        self.force_vi()
        before = self.mgr.game_for(CH).to_json()
        for text in ("fuck", "**Fuck!**", "shit", "hell"):
            with self.subTest(text=text):
                self.assertIsNone(self.play(text))
        self.assertEqual(self.mgr.game_for(CH).to_json(), before)
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)
        self.assertEqual(self.store.get_player(1, "vi")["wrong"], 0)
        # A 2-syllable answer containing an English swear word is never BANNED in Vietnamese.
        result = self.play("sinh hell")
        self.assertTrue(result is None or result.status != PhraseStatus.BANNED)
        self.assertEqual(self.play("sinh viên", 2, "Ann").status, PhraseStatus.OK)

    def test_offensive_chat_ignored_in_english_game(self):
        self.force_en()
        self.assertIsNone(self.play("fuck you"))
        self.assertEqual(self.store.get_player(1, "en")["wrong"], 0)

    # ------------------------------------------------------------ wins start a round in the same language
    def test_vietnamese_win_starts_vietnamese_round(self):
        self.force_vi()
        for i, word in enumerate(("sinh viên", "viên chức", "chức năng"), start=1):
            r = self.play(word, user_id=i)
        self.assertEqual(r.status, PhraseStatus.WIN)
        if self.mgr.game_for(CH) is not None:
            self.assertEqual(self.mgr.game_for(CH).language, "vi")

    def test_english_win_starts_english_round(self):
        self.mgr._en_dictionary = self.en = make_en(["yak", "kiwi", "tiger", "rabbit", "train", "night"])
        self.force_en(letter="y", phrase="tiger")
        self.play("yak", 1)
        r = self.play("kiwi", 2)
        self.assertEqual(r.status, PhraseStatus.WIN)
        if self.mgr.game_for(CH) is not None:
            self.assertEqual(self.mgr.game_for(CH).language, "en")

    # ------------------------------------------------------------ admin dictionary edits mid-game
    def test_admin_edits_during_english_game(self):
        self.force_en()
        game_before = self.mgr.game_for(CH).to_json()
        en_before = snapshot_en(self.en)
        self.assertTrue(self.mgr.add_word("năng lực", 9)[0])                 # Vietnamese word
        self.assertTrue(self.mgr.remove_word("viên chức", 9)[0])
        self.assertEqual(snapshot_en(self.en), en_before)                    # English untouched
        self.assertEqual(self.mgr.game_for(CH).to_json(), game_before)
        self.assertIn("năng lực", self.vi.words.values())
        self.assertNotIn("viên chức", self.vi.words.values())

    def test_admin_edits_during_vietnamese_game(self):
        self.force_vi()
        game_before = self.mgr.game_for(CH).to_json()
        vi_before = snapshot_vi(self.vi)
        self.assertTrue(self.mgr.add_word("zyzzyva", 9)[0])                  # English word
        self.assertTrue(self.mgr.remove_word("tiger", 9)[0])
        self.assertEqual(snapshot_vi(self.vi), vi_before)                    # Vietnamese untouched
        self.assertEqual(self.mgr.game_for(CH).to_json(), game_before)
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
        g = self.mgr.game_for(CH)
        self.assertEqual((g.language, g.expected_start_key), ("en", "g"))
        self.assertEqual(self.play("goat", 2).status, PhraseStatus.OK)
        self.mgr.stop_game(CH)

        self.force_vi()
        self.play("sinh viên")
        self.mgr = self.new_manager()
        g = self.mgr.game_for(CH)
        self.assertEqual((g.language, g.expected_start_key), ("vi", syllable_key("viên")))
        self.assertEqual(self.play("viên chức", 2).status, PhraseStatus.OK)

    # ------------------------------------------------------------ stats per language, hints shared
    def test_stats_are_separate_hint_limit_is_shared(self):
        self.force_en()
        self.play("egg")
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertTrue(ok)
        self.assertIn("4/5", message)
        self.mgr.stop_game(CH)
        self.force_vi()
        self.play("sinh viên")
        ok, message = self.mgr.give_hint(CH, 1)
        self.assertTrue(ok)
        self.assertIn("3/5", message)
        expected = {"correct": 1, "wrong": 0, "wins": 0}
        self.assertEqual(self.store.get_player(1, "en"), expected)
        self.assertEqual(self.store.get_player(1, "vi"), expected)
        self.assertEqual(self.store.hints_used(1, today()), 2)

    def test_wins_count_in_game_language(self):
        self.mgr._en_dictionary = self.en = make_en(["yak", "kiwi", "tiger", "rabbit", "train", "night"])
        self.force_en(letter="k", phrase="yak")
        self.assertEqual(self.play("kiwi", 5).status, PhraseStatus.WIN)
        self.assertEqual(self.store.get_player(5, "en")["wins"], 1)
        self.assertEqual(self.store.get_player(5, "vi")["wins"], 0)
        if self.mgr.has_game(CH):
            self.mgr.stop_game(CH)

        self.force_vi("viên chức")
        self.assertEqual(self.play("chức năng", 5).status, PhraseStatus.WIN)
        self.assertEqual(self.store.get_player(5, "vi")["wins"], 1)
        self.assertEqual(self.store.get_player(5, "en")["wins"], 1)


class CrossLanguageVietnameseUi(CrossLanguageCase):
    ui = "vi"


if __name__ == "__main__":
    unittest.main()
