"""Multi-channel tests: each channel runs its own game, and games in different channels
(any language) never affect each other. Also the v1 -> v2 storage migration (one saved game
per channel).

Run from the repo root: python -m unittest discover -s tests
"""
import gc
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage import SCHEMA_VERSION, GameStore  # noqa: E402
from vi_text import parse_word, syllable_key  # noqa: E402
from word_chain import (  # noqa: E402
    ChannelSession,
    PhraseStatus,
    SkipStatus,
    WordChainGameManager,
    WordChainState,
)

from test_en_word_chain import EN_WORDS, make_en, make_vi, today  # noqa: E402
from test_language_stats import OLD_SCHEMA, make_old_db, table_names, user_version  # noqa: E402


A, B, C = 100, 101, 102

TEXTS = {
    "en": {
        "already": (
            "❌ **A word-chain game is already running in this channel**\n"
            "> Stop the current game before starting a new one."
        ),
        "stop_none": "❌ **No active word-chain game**\n> There is no running game in this channel.",
        "status_none": "❌ **No active word-chain game in this channel**\n> There is no running game in this channel.",
        "channel_none": "❌ **No active word-chain game in this channel**",
        "en_shape": "❌ **An English word must be one word of 3+ letters (a–z only)**",
        "vi_shape": "❌ **A Vietnamese word must have exactly 2 syllables** (letters only)",
        "hoc_missing": "❌ **hoc** is not in the dictionary.",
        "board_en": "🏅 **Word-chain leaderboard: English**\n",
        "board_vi": "🏅 **Word-chain leaderboard: Vietnamese**\n",
        "used_all": "used all 5 hints",
    },
    "vi": {
        "already": (
            "❌ **Kênh này đã có trò chơi nối từ đang hoạt động**\n"
            "> Hãy dừng trò chơi hiện tại trước khi bắt đầu trò mới."
        ),
        "stop_none": "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào.",
        "status_none": "❌ **Không có trò chơi nối từ nào đang hoạt động**\n> Trong kênh này hiện chưa có trò chơi nào.",
        "channel_none": "❌ **Không có trò chơi nối từ nào đang hoạt động trong kênh này**",
        "en_shape": "❌ **Từ tiếng Anh phải là một từ, từ 3 chữ cái trở lên (chỉ a–z)**",
        "vi_shape": "❌ **Từ phải gồm đúng 2 âm tiết** (chỉ có chữ cái)",
        "hoc_missing": "❌ **hoc** không có trong từ điển.",
        "board_en": "🏅 **Bảng xếp hạng nối từ: Tiếng Anh**\n",
        "board_vi": "🏅 **Bảng xếp hạng nối từ: Tiếng Việt**\n",
        "used_all": "Bạn đã dùng hết 5 lượt gợi ý hôm nay",
    },
}


def vi_key(display: str) -> str:
    return parse_word(display)[0]


def en_state(letter="e", phrase="apple", used=None) -> WordChainState:
    return WordChainState(
        current_phrase=phrase,
        expected_start_word=letter,
        language="en",
        expected_start_key=letter,
        used=dict(used) if used is not None else {phrase: None},
    )


def vi_state(word="sinh", phrase="học sinh", used=None) -> WordChainState:
    return WordChainState(
        current_phrase=phrase,
        expected_start_word=word,
        language="vi",
        expected_start_key=syllable_key(word),
        used=dict(used) if used is not None else {vi_key(phrase): None},
    )


class MultiChannelCase(unittest.TestCase):
    ui = "en"

    def setUp(self) -> None:
        self.store = GameStore(":memory:")
        self.en = make_en()
        self.vi = make_vi()
        # The 4-word test list is too small for the real starter rules, so pin one starter.
        self.vi._start_pool = [vi_key("học sinh")]
        self.mgr = self.manager()
        self.t = TEXTS[self.ui]

    def manager(self, en=None) -> WordChainGameManager:
        return WordChainGameManager(
            default_language="en", ui_language=self.ui, store=self.store,
            vi_dictionary=self.vi, en_dictionary=en if en is not None else self.en,
        )

    def put(self, channel_id: int, game: WordChainState) -> WordChainState:
        """Run a known game in a channel and save it, like start_game does."""
        self.mgr._sessions[channel_id] = ChannelSession(game)
        self.mgr._save(channel_id)
        return game

    def play(self, channel_id, text, user_id=1, name="Bob"):
        return self.mgr.handle_player_phrase(channel_id, user_id, name, text)

    def snapshot(self, channel_id):
        """The channel's session state and its stored row."""
        session = self.mgr._sessions[channel_id]
        return (
            session.game.to_json(), set(session.skip_votes), dict(session.last_answer_at),
            self.store.load_game(channel_id),
        )

    # ------------------------------------------------------------ 1. two languages at once
    def test_01_english_and_vietnamese_at_once(self):
        self.put(A, en_state())
        self.put(B, vi_state())
        self.assertEqual(self.play(A, "egg").status, PhraseStatus.OK)
        self.assertEqual(self.play(B, "sinh viên", 2, "Ann").status, PhraseStatus.OK)
        self.assertEqual(self.play(A, "goat", 3, "Cy").status, PhraseStatus.OK)

        # Text of the other language is chat in each channel.
        before_a, before_b = self.snapshot(A), self.snapshot(B)
        for text in ("sinh viên", "viên chức", "học sinh"):
            with self.subTest(channel=A, text=text):
                self.assertIsNone(self.play(A, text, 4))
        for text in ("egg", "tiger", "**Egg!**"):
            with self.subTest(channel=B, text=text):
                self.assertIsNone(self.play(B, text, 4))
        self.assertEqual(self.snapshot(A), before_a)
        self.assertEqual(self.snapshot(B), before_b)
        self.assertEqual(self.store.get_player(4, "en"), {"correct": 0, "wrong": 0, "wins": 0})
        self.assertEqual(self.store.get_player(4, "vi"), {"correct": 0, "wrong": 0, "wins": 0})

        game_a, game_b = self.mgr.game_for(A), self.mgr.game_for(B)
        self.assertEqual((game_a.language, game_a.turns, set(game_a.used)), ("en", 2, {"apple", "egg", "goat"}))
        self.assertEqual(game_a.expected_start_key, "t")
        self.assertEqual(
            (game_b.language, game_b.turns, set(game_b.used)),
            ("vi", 1, {vi_key("học sinh"), vi_key("sinh viên")}),
        )
        self.assertEqual(game_b.expected_start_key, syllable_key("viên"))
        self.assertEqual(json.loads(self.store.load_game(A))["language"], "en")
        self.assertEqual(json.loads(self.store.load_game(B))["language"], "vi")
        self.assertEqual(self.store.get_player(1, "en")["correct"], 1)
        self.assertEqual(self.store.get_player(2, "vi")["correct"], 1)
        self.assertEqual(self.store.get_player(2, "en")["correct"], 0)

    # ------------------------------------------------------------ 2. cooldown
    def test_02_cooldown_is_per_channel(self):
        self.put(A, en_state())
        self.put(B, vi_state())
        self.put(C, en_state())
        self.assertEqual(self.play(A, "egg").status, PhraseStatus.OK)
        # The same player answers right away in the other channels.
        self.assertEqual(self.play(B, "sinh viên").status, PhraseStatus.OK)
        self.assertEqual(self.play(C, "egg").status, PhraseStatus.OK)
        # ... but is still on cooldown in each of them.
        self.assertEqual(self.play(A, "goat").status, PhraseStatus.COOLDOWN)
        self.assertEqual(self.play(B, "viên chức").status, PhraseStatus.COOLDOWN)
        self.assertEqual(self.play(C, "goat").status, PhraseStatus.COOLDOWN)
        for channel_id in (A, B, C):
            self.assertEqual(set(self.mgr._sessions[channel_id].last_answer_at), {1})
        # Another player is free in every channel.
        self.assertEqual(self.play(A, "goat", 2, "Ann").status, PhraseStatus.OK)
        self.assertEqual(set(self.mgr._sessions[C].last_answer_at), {1})

    # ------------------------------------------------------------ 3. skip votes
    def test_03_skip_votes_are_per_channel(self):
        self.put(A, en_state())
        self.put(B, vi_state("viên", "sinh viên"))
        self.assertEqual(self.play(A, "egg", 3, "Cy").status, PhraseStatus.OK)
        self.assertEqual(self.mgr.vote_skip(A, 1, "Bob")[0], SkipStatus.VOTED)
        self.assertEqual(self.mgr.vote_skip(B, 2, "Ann")[0], SkipStatus.VOTED)
        # A vote in A does not count in B, so player 1 can still vote in B.
        self.assertEqual(self.mgr._sessions[A].skip_votes, {1})
        self.assertEqual(self.mgr._sessions[B].skip_votes, {2})
        before_a = self.snapshot(A)

        status, message = self.mgr.vote_skip(B, 1, "Bob")
        self.assertEqual(status, SkipStatus.SKIPPED)
        self.assertIn("`viên chức`", message)
        new_b = self.mgr._sessions[B]
        self.assertEqual((new_b.skip_votes, new_b.last_answer_at), (set(), {}))
        self.assertEqual((new_b.game.language, new_b.game.current_phrase, new_b.game.turns), ("vi", "học sinh", 0))
        self.assertEqual(json.loads(self.store.load_game(B))["current_phrase"], "học sinh")
        # A keeps its game, its vote and its cooldown.
        self.assertEqual(self.snapshot(A), before_a)
        self.assertEqual(self.mgr._sessions[A].skip_votes, {1})
        self.assertIn(3, self.mgr._sessions[A].last_answer_at)
        # A second vote in A skips A only.
        self.assertEqual(self.mgr.vote_skip(A, 2, "Ann")[0], SkipStatus.SKIPPED)
        self.assertIs(self.mgr._sessions[B], new_b)
        self.assertEqual(self.mgr._sessions[A].skip_votes, set())

    # ------------------------------------------------------------ 4. wins
    def test_04_win_new_round_leaves_other_channel(self):
        self.mgr = self.manager(make_en(EN_WORDS + ["kiwi"]))
        self.put(A, en_state("k", "yak"))
        self.put(B, vi_state())
        self.assertEqual(self.play(B, "sinh viên", 2, "Ann").status, PhraseStatus.OK)
        self.assertEqual(self.mgr.vote_skip(B, 3, "Cy")[0], SkipStatus.VOTED)
        session_b, before_b = self.mgr._sessions[B], self.snapshot(B)

        result = self.play(A, "kiwi")
        self.assertEqual(result.status, PhraseStatus.WIN)
        self.assertIn("🎮", result.message)
        game_a = self.mgr.game_for(A)
        self.assertEqual((game_a.language, game_a.turns), ("en", 0))
        self.assertEqual(json.loads(self.store.load_game(A))["current_phrase"], game_a.current_phrase)
        self.assertIs(self.mgr._sessions[B], session_b)
        self.assertEqual(self.snapshot(B), before_b)
        self.assertEqual(self.store.get_player(1, "en")["wins"], 1)

    def test_04b_win_without_starter_leaves_other_channel(self):
        self.mgr = self.manager(make_en(["yak", "kiwi"]))  # no starter word: the game ends
        self.put(A, en_state("k", "yak"))
        self.put(B, vi_state())
        self.assertEqual(self.play(B, "sinh viên", 2, "Ann").status, PhraseStatus.OK)
        before_b = self.snapshot(B)

        self.assertEqual(self.play(A, "kiwi").status, PhraseStatus.WIN)
        self.assertFalse(self.mgr.has_game(A))
        self.assertIsNone(self.store.load_game(A))
        self.assertTrue(self.mgr.has_game(B))
        self.assertEqual(self.snapshot(B), before_b)
        self.assertEqual(self.store.load_games(), [(B, before_b[3])])
        self.assertEqual(self.play(B, "viên chức", 3, "Cy").status, PhraseStatus.OK)

    # ------------------------------------------------------------ 5. stop / status
    def test_05_stop_and_status_per_channel(self):
        self.put(A, en_state())
        self.put(B, vi_state())
        before_b = self.snapshot(B)
        ok, message = self.mgr.stop_game(A)
        self.assertTrue(ok)
        self.assertFalse(self.mgr.has_game(A))
        self.assertIsNone(self.store.load_game(A))
        self.assertEqual(self.snapshot(B), before_b)
        self.assertTrue(self.mgr.game_status(B)[0])

        messages = [
            (self.mgr.stop_game(A), (False, self.t["stop_none"])),
            (self.mgr.stop_game(C), (False, self.t["stop_none"])),
            (self.mgr.game_status(A), (False, self.t["status_none"])),
            (self.mgr.game_status(C), (False, self.t["status_none"])),
            (self.mgr.vote_skip(C, 1, "Bob"), (SkipStatus.ERROR, self.t["channel_none"])),
            (self.mgr.give_hint(C, 1), (False, self.t["channel_none"])),
        ]
        for actual, expected in messages:
            with self.subTest(expected=expected):
                self.assertEqual(actual, expected)
                self.assertNotIn("another channel", actual[1])
                self.assertNotIn("kênh khác", actual[1])
        self.assertIsNone(self.play(C, "egg"))
        self.assertIsNone(self.play(C, "sinh viên"))
        self.assertEqual(self.store.load_games(), [(B, before_b[3])])

    # ------------------------------------------------------------ 6. hints
    def test_06_hints_per_channel_language_shared_counter(self):
        self.put(A, en_state())
        self.put(B, vi_state("viên", "sinh viên"))
        ok, message = self.mgr.give_hint(A, 1)
        self.assertTrue(ok)
        self.assertRegex(message, r"`e_+`")
        self.assertIn("4/5", message)
        ok, message = self.mgr.give_hint(B, 1)
        self.assertTrue(ok)
        self.assertIn("`viên c___`", message)
        self.assertIn("3/5", message)
        # No game in C: refused and not counted.
        self.assertEqual(self.mgr.give_hint(C, 1), (False, self.t["channel_none"]))
        self.assertEqual(self.store.hints_used(1, today()), 2)
        for channel_id in (A, B, A):
            self.assertTrue(self.mgr.give_hint(channel_id, 1)[0])
        ok, message = self.mgr.give_hint(B, 1)
        self.assertFalse(ok)
        self.assertIn(self.t["used_all"], message)
        self.assertEqual(self.store.hints_used(1, today()), 5)
        # Hints change no game.
        self.assertEqual(self.mgr.game_for(A).turns + self.mgr.game_for(B).turns, 0)

    # ------------------------------------------------------------ 7. two Vietnamese games
    def test_07_two_vietnamese_games(self):
        self.put(A, vi_state())
        self.put(B, vi_state())
        # The same word is new in each channel.
        self.assertEqual(self.play(A, "sinh viên", 1).status, PhraseStatus.OK)
        self.assertEqual(self.play(B, "sinh viên", 2).status, PhraseStatus.OK)
        before_b = self.snapshot(B)
        self.assertEqual(self.play(A, "viên chức", 3).status, PhraseStatus.OK)
        result = self.play(A, "chức năng", 4)
        self.assertEqual(result.status, PhraseStatus.WIN)
        game_a = self.mgr.game_for(A)
        self.assertEqual((game_a.current_phrase, game_a.turns), ("học sinh", 0))
        self.assertEqual(self.snapshot(B), before_b)
        # B goes on with the words A used in its finished round.
        self.assertEqual(self.play(B, "viên chức", 3).status, PhraseStatus.OK)
        self.assertEqual(self.play(B, "chức năng", 4, "Dee").status, PhraseStatus.WIN)
        self.assertEqual(self.store.get_player(4, "vi")["wins"], 2)

    # ------------------------------------------------------------ 8. starting
    def test_08_start_refused_only_in_the_same_channel(self):
        ok, _ = self.mgr.start_game(A, "en")
        self.assertTrue(ok)
        before_a = self.snapshot(A)
        for language in ("vi", "en", None):
            with self.subTest(language=language):
                self.assertEqual(self.mgr.start_game(A, language), (False, self.t["already"]))
        self.assertEqual(self.snapshot(A), before_a)
        ok, message = self.mgr.start_game(B, "vi")
        self.assertTrue(ok)
        self.assertIn("`học sinh`", message)
        ok, _ = self.mgr.start_game(C, "en")
        self.assertTrue(ok)
        self.assertEqual([channel_id for channel_id, _ in self.store.load_games()], [A, B, C])
        self.assertEqual(self.snapshot(A), before_a)
        self.assertEqual(self.mgr.start_game(B, "en"), (False, self.t["already"]))
        self.assertEqual(self.mgr.game_for(B).language, "vi")

    # ------------------------------------------------------------ 9. restart
    def test_09_restart_resumes_every_channel(self):
        self.put(A, en_state())
        self.put(B, vi_state())
        self.play(A, "egg")
        self.play(B, "sinh viên")
        self.mgr.vote_skip(A, 3, "Cy")
        self.mgr.vote_skip(B, 3, "Cy")
        # A game saved by the old phrase-based English mode.
        self.store.save_game(C, json.dumps({
            "current_phrase": "king maker", "expected_start_word": "maker", "language": "en",
            "expected_start_key": "maker", "used": {"king maker": "Bob"}, "last_player_id": 1, "turns": 3,
        }))

        self.mgr = self.manager()
        self.assertEqual(set(self.mgr._sessions), {A, B, C})
        for channel_id in (A, B, C):
            session = self.mgr._sessions[channel_id]
            self.assertEqual((session.skip_votes, session.last_answer_at), (set(), {}))
        self.assertEqual(self.mgr.game_for(A).expected_start_key, "g")
        self.assertEqual(self.mgr.game_for(B).expected_start_key, syllable_key("viên"))
        game_c = self.mgr.game_for(C)
        self.assertEqual((game_c.expected_start_word, game_c.expected_start_key, game_c.turns), ("r", "r", 3))
        self.assertEqual(json.loads(self.store.load_game(C))["expected_start_key"], "r")
        # No cooldown or vote survives the restart.
        self.assertEqual(self.play(A, "goat").status, PhraseStatus.OK)
        self.assertEqual(self.play(B, "viên chức").status, PhraseStatus.OK)
        self.assertEqual(self.play(C, "rabbit").status, PhraseStatus.OK)
        self.assertEqual(self.mgr.vote_skip(A, 3, "Cy")[0], SkipStatus.VOTED)

    def test_09b_bad_rows_dropped_other_channels_survive(self):
        self.put(A, vi_state())
        good_row = self.store.load_game(A)
        self.store.save_game(B, json.dumps({
            "current_phrase": "123", "expected_start_word": "123", "language": "en",
            "expected_start_key": "123", "used": {},
        }))
        self.store.save_game(C, "{not json")
        self.store.save_game(103, json.dumps({"current_phrase": "apple"}))  # missing fields
        self.store.save_game(104, json.dumps(["apple"]))  # not an object

        with self.assertLogs("hqs-bot", "WARNING") as logs:
            self.mgr = self.manager()
        self.assertEqual(len(logs.records), 4)
        self.assertEqual(set(self.mgr._sessions), {A})
        self.assertEqual(self.store.load_games(), [(A, good_row)])
        for channel_id in (B, C, 103, 104):
            self.assertFalse(self.mgr.has_game(channel_id))
        self.assertEqual(self.play(A, "sinh viên").status, PhraseStatus.OK)
        self.assertTrue(self.mgr.start_game(C, "en")[0])

    # ------------------------------------------------------------ 10. stats, leaderboard, check
    def test_10_stats_and_defaults_per_channel(self):
        self.put(A, en_state())
        self.put(B, vi_state())
        self.put(C, en_state())
        self.assertEqual(self.play(A, "egg").status, PhraseStatus.OK)
        self.assertEqual(self.play(C, "egg").status, PhraseStatus.OK)
        self.assertEqual(self.play(C, "gqqq", 2, "Ann").status, PhraseStatus.NOT_IN_DICT)
        self.assertEqual(self.play(B, "học sinh").status, PhraseStatus.WRONG_START)
        self.assertEqual(self.play(B, "sinh viên").status, PhraseStatus.OK)
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 2, "wrong": 0, "wins": 0})
        self.assertEqual(self.store.get_player(1, "vi"), {"correct": 1, "wrong": 1, "wins": 0})
        self.assertEqual(self.store.get_player(2, "en"), {"correct": 0, "wrong": 1, "wins": 0})

        self.assertTrue(self.mgr.leaderboard(channel_id=A).startswith(self.t["board_en"]))
        board_b = self.mgr.leaderboard(channel_id=B)
        self.assertTrue(board_b.startswith(self.t["board_vi"]))
        self.assertNotIn("<@2>", board_b)
        self.assertTrue(self.mgr.leaderboard(channel_id=999).startswith(self.t["board_en"]))  # default
        self.assertTrue(self.mgr.leaderboard().startswith(self.t["board_en"]))
        self.assertTrue(self.mgr.leaderboard("vi", channel_id=A).startswith(self.t["board_vi"]))

        self.assertEqual(self.mgr.check_word("ok", channel_id=A), (False, self.t["en_shape"]))
        self.assertEqual(self.mgr.check_word("ok", channel_id=B), (False, self.t["vi_shape"]))
        self.assertEqual(self.mgr.check_word("học", channel_id=B), (False, self.t["vi_shape"]))
        self.assertEqual(self.mgr.check_word("học", channel_id=999), (False, self.t["hoc_missing"]))
        self.assertEqual(self.mgr.check_word("học"), (False, self.t["hoc_missing"]))
        # Shape routing still wins over the channel language.
        self.assertTrue(self.mgr.check_word("apple", channel_id=B)[0])
        self.assertTrue(self.mgr.check_word("sinh viên", channel_id=A)[0])


class MultiChannelViUi(MultiChannelCase):
    ui = "vi"


# ---------------------------------------------------------------- 11. store and migration
STATE = json.dumps({
    "current_phrase": "học sinh", "expected_start_word": "sinh", "language": "vi",
    "expected_start_key": "sinh", "used": {}, "last_player_id": None, "turns": 4,
}, ensure_ascii=False)

PLAYER_STATS = """
CREATE TABLE player_stats (
    user_id  INTEGER NOT NULL,
    language TEXT NOT NULL,
    correct  INTEGER NOT NULL DEFAULT 0,
    wrong    INTEGER NOT NULL DEFAULT 0,
    wins     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, language)
);
"""


class ChannelStoreTests(unittest.TestCase):
    def test_save_load_delete(self):
        store = GameStore(":memory:")
        self.assertEqual(store.load_games(), [])
        self.assertIsNone(store.load_game(A))
        store.save_game(B, "b1")
        store.save_game(A, "a1")
        store.save_game(B, "b2")  # upsert
        self.assertEqual(store.load_games(), [(A, "a1"), (B, "b2")])
        self.assertEqual(store.load_game(B), "b2")
        store.delete_game(A)
        store.delete_game(C)  # nothing saved there: no error
        self.assertIsNone(store.load_game(A))
        self.assertEqual(store.load_games(), [(B, "b2")])
        # The legacy table is never written.
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM active_game").fetchone()[0], 0)


class ChannelMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "word_chain.db"

    def open_store(self) -> GameStore:
        store = GameStore(self.path)
        self.addCleanup(store.conn.close)
        return store

    def raw(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def make_v1_db(self) -> None:
        """A database as the single-game version left it: user_version 1, one active_game row."""
        conn = sqlite3.connect(self.path)
        conn.executescript(OLD_SCHEMA + PLAYER_STATS)
        conn.execute("INSERT INTO players VALUES (1, 50, 5, 9)")
        conn.execute("INSERT INTO player_stats VALUES (1, 'vi', 5, 2, 1)")
        conn.execute("INSERT INTO active_game (id, channel_id, state) VALUES (1, 555, ?)", (STATE,))
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

    def test_v1_to_v2(self):
        self.make_v1_db()
        store = GameStore(self.path)
        self.assertEqual(user_version(store.conn), SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(store.load_game(555), STATE)
        self.assertEqual(store.load_games(), [(555, STATE)])
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM active_game").fetchone()[0], 0)
        # The v0 -> v1 step does not run again.
        self.assertEqual(store.get_player(1, "vi"), {"correct": 5, "wrong": 2, "wins": 1})

        mgr = WordChainGameManager(
            default_language="en", store=store, vi_dictionary=make_vi(), en_dictionary=make_en()
        )
        self.assertTrue(mgr.has_game(555))
        self.assertEqual(mgr.game_for(555).turns, 4)
        self.assertEqual(mgr.handle_player_phrase(555, 1, "Bob", "sinh viên").status, PhraseStatus.OK)
        store.conn.close()

        store = GameStore(self.path)  # reopen: nothing copied twice
        self.assertEqual([channel_id for channel_id, _ in store.load_games()], [555])
        self.assertEqual(json.loads(store.load_game(555))["turns"], 5)
        mgr = WordChainGameManager(store=store, vi_dictionary=make_vi(), en_dictionary=make_en())
        self.assertTrue(mgr.stop_game(555)[0])
        store.conn.close()

        store = self.open_store()  # a stopped game is not brought back
        self.assertEqual(store.load_games(), [])
        self.assertEqual(store.get_player(1, "vi")["correct"], 6)

    def test_v0_runs_both_steps(self):
        make_old_db(self.path)
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO active_game (id, channel_id, state) VALUES (1, 555, ?)", (STATE,))
        conn.commit()
        conn.close()
        store = self.open_store()
        self.assertEqual(user_version(store.conn), SCHEMA_VERSION)
        self.assertEqual(store.get_player(1, "vi"), {"correct": 5, "wrong": 2, "wins": 1})
        self.assertEqual(store.load_games(), [(555, STATE)])
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM active_game").fetchone()[0], 0)

    def test_broken_v0_rolls_back_both_steps(self):
        # An old players table without a wins column makes the first step fail.
        broken_schema = OLD_SCHEMA.replace(",\n    wins    INTEGER NOT NULL DEFAULT 0", "")
        make_old_db(self.path, rows=(), schema=broken_schema)
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO players (user_id, correct, wrong) VALUES (1, 5, 2)")
        conn.execute("INSERT INTO active_game (id, channel_id, state) VALUES (1, 555, ?)", (STATE,))
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.OperationalError):
            GameStore(self.path)
        gc.collect()  # close the failed store's connection
        raw = self.raw()
        self.assertEqual(user_version(raw), 0)
        self.assertIn("channel_games", table_names(raw))
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0], 0)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM channel_games").fetchone()[0], 0)
        self.assertEqual(raw.execute("SELECT channel_id, state FROM active_game").fetchall(), [(555, STATE)])


if __name__ == "__main__":
    unittest.main()
