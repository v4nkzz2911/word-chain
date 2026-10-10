"""Separate stats and ranks for English and Vietnamese, the v0 -> v1 database migration,
the per-language profile / leaderboard texts and the help text.

Run from the repo root: python -m unittest discover -s tests
"""
import gc
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from storage import SCHEMA_VERSION, GameStore  # noqa: E402
from vi_text import syllable_key  # noqa: E402
from word_chain import ChannelSession, WordChainGameManager, WordChainState  # noqa: E402

from test_en_word_chain import CH, make_en, make_vi  # noqa: E402

ZERO = {"correct": 0, "wrong": 0, "wins": 0}

OLD_SCHEMA = """
CREATE TABLE active_game (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    channel_id INTEGER NOT NULL,
    state      TEXT NOT NULL
);
CREATE TABLE players (
    user_id INTEGER PRIMARY KEY,
    correct INTEGER NOT NULL DEFAULT 0,
    wrong   INTEGER NOT NULL DEFAULT 0,
    wins    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE custom_words (
    word    TEXT PRIMARY KEY,
    added   INTEGER NOT NULL,
    by_user INTEGER
);
CREATE TABLE hint_usage (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    used    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""


def user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def table_names(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def make_old_db(path: Path, rows=((1, 5, 2, 1), (2, 0, 3, 0)), schema=OLD_SCHEMA) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    if rows:
        conn.executemany("INSERT INTO players (user_id, correct, wrong, wins) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def players_rows(conn) -> list[tuple]:
    return [tuple(row) for row in conn.execute("SELECT user_id, correct, wrong, wins FROM players ORDER BY user_id")]


# ---------------------------------------------------------------- migration
class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "word_chain.db"

    def open_store(self) -> GameStore:
        store = GameStore(self.path)
        self.addCleanup(store.conn.close)
        return store

    def test_fresh_database(self):
        for store in (self.open_store(), GameStore(":memory:")):
            with self.subTest(store=store):
                self.assertEqual(user_version(store.conn), SCHEMA_VERSION)
                self.assertEqual(SCHEMA_VERSION, 2)
                self.assertTrue({"players", "player_stats"} <= table_names(store.conn))
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0], 0)

    def test_old_stats_become_vietnamese(self):
        make_old_db(self.path)
        store = self.open_store()
        self.assertEqual(store.get_player(1, "vi"), {"correct": 5, "wrong": 2, "wins": 1})
        self.assertEqual(store.get_player(2, "vi"), {"correct": 0, "wrong": 3, "wins": 0})
        self.assertEqual(store.get_player(1, "en"), ZERO)
        self.assertEqual(store.get_player(2, "en"), ZERO)
        self.assertEqual(store.top("en"), [])
        self.assertEqual(store.rank_of(1, "vi"), 1)
        self.assertEqual(store.rank_of(2, "vi"), 2)
        self.assertIsNone(store.rank_of(1, "en"))
        self.assertEqual(user_version(store.conn), SCHEMA_VERSION)

    def test_reopen_does_not_copy_again(self):
        make_old_db(self.path)
        store = GameStore(self.path)
        store.bump(1, "correct", "vi")
        store.conn.close()

        raw = sqlite3.connect(self.path)
        raw.execute("UPDATE players SET correct = 99")
        raw.commit()
        raw.close()

        store = self.open_store()
        self.assertEqual(store.get_player(1, "vi")["correct"], 6)
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0], 2)

    def test_old_players_table_untouched(self):
        rows = [(1, 5, 2, 1), (2, 0, 3, 0)]
        make_old_db(self.path, rows)
        store = self.open_store()
        store.bump(1, "wins", "vi")
        store.bump(1, "correct", "en")
        store.bump(3, "wrong", "en")
        self.assertEqual(players_rows(store.conn), rows)

    def test_failed_migration_rolls_back(self):
        # An old players table without a wins column makes the copy fail half-way.
        broken_schema = OLD_SCHEMA.replace(",\n    wins    INTEGER NOT NULL DEFAULT 0", "")
        make_old_db(self.path, rows=(), schema=broken_schema)
        raw = sqlite3.connect(self.path)
        raw.execute("INSERT INTO players (user_id, correct, wrong) VALUES (1, 5, 2)")
        raw.commit()
        raw.close()
        with self.assertRaises(sqlite3.OperationalError):
            GameStore(self.path)
        gc.collect()  # close the failed store's connection
        raw = sqlite3.connect(self.path)
        self.addCleanup(raw.close)
        self.assertEqual(user_version(raw), 0)
        self.assertEqual(raw.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0], 0)


# ---------------------------------------------------------------- store isolation
class StoreIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = GameStore(":memory:")

    def test_separate_stats_and_top(self):
        self.store.bump(1, "wins", "en")
        self.store.bump(1, "wins", "en")
        for _ in range(3):
            self.store.bump(1, "correct", "vi")
        self.assertEqual(self.store.get_player(1, "en"), {"correct": 0, "wrong": 0, "wins": 2})
        self.assertEqual(self.store.get_player(1, "vi"), {"correct": 3, "wrong": 0, "wins": 0})
        self.assertEqual([tuple(r) for r in self.store.top("en")], [(1, 0, 0, 2)])
        self.assertEqual([tuple(r) for r in self.store.top("vi")], [(1, 3, 0, 0)])

    def test_ranks_per_language(self):
        self.store.bump(1, "wins", "en", 2)
        self.store.bump(2, "wins", "en")
        self.store.bump(2, "wins", "vi", 5)
        self.assertEqual(self.store.rank_of(1, "en"), 1)
        self.assertEqual(self.store.rank_of(2, "en"), 2)
        self.assertEqual(self.store.rank_of(2, "vi"), 1)
        self.assertIsNone(self.store.rank_of(1, "vi"))

    def test_invalid_language_and_field(self):
        for language in ("fr", "xx", "", "EN"):
            with self.subTest(language=language):
                with self.assertRaises(ValueError):
                    self.store.bump(1, "correct", language)
                with self.assertRaises(ValueError):
                    self.store.get_player(1, language)
                with self.assertRaises(ValueError):
                    self.store.top(language)
                with self.assertRaises(ValueError):
                    self.store.rank_of(1, language)
        with self.assertRaises(ValueError):
            self.store.bump(1, "points", "en")


# ---------------------------------------------------------------- manager texts
EXPECTED = {
    "en": {
        "header": "👤 **Word-chain profile of Bob**",
        "vi_title": "🌐 **Vietnamese**",
        "en_title": "🌐 **English**",
        "empty": "> No games yet",
        "vi_section": (
            "> 🏆 **Wins:** `0`\n"
            "> ✅ **Correct:** `1`\n"
            "> ❌ **Wrong:** `0`\n"
            "> 🎯 **Accuracy:** `100%`\n"
            "> 📊 **Rank:** `#1`"
        ),
        "en_section": (
            "> 🏆 **Wins:** `1`\n"
            "> ✅ **Correct:** `2`\n"
            "> ❌ **Wrong:** `2`\n"
            "> 🎯 **Accuracy:** `50%`\n"
            "> 📊 **Rank:** `#1`"
        ),
        "no_store": "❌ **Stats are not available**",
        "board_en": "🏅 **Word-chain leaderboard: English**\n🥇 <@2> — **1** wins · 1 correct",
        "board_title_en": "🏅 **Word-chain leaderboard: English**\n",
        "board_title_vi": "🏅 **Word-chain leaderboard: Vietnamese**\n",
        "empty_en": "📭 **No English games played yet**\n> Start a game and be the first!",
        "empty_vi": "📭 **No Vietnamese games played yet**\n> Start a game and be the first!",
        "unsupported": "❌ **Unsupported language**\n> Use `en` (English) or `vi` (Vietnamese).",
    },
    "vi": {
        "header": "👤 **Hồ sơ nối từ của Bob**",
        "vi_title": "🌐 **Tiếng Việt**",
        "en_title": "🌐 **Tiếng Anh**",
        "empty": "> Chưa chơi",
        "vi_section": (
            "> 🏆 **Thắng:** `0`\n"
            "> ✅ **Từ đúng:** `1`\n"
            "> ❌ **Từ sai:** `0`\n"
            "> 🎯 **Độ chính xác:** `100%`\n"
            "> 📊 **Hạng:** `#1`"
        ),
        "en_section": (
            "> 🏆 **Thắng:** `1`\n"
            "> ✅ **Từ đúng:** `2`\n"
            "> ❌ **Từ sai:** `2`\n"
            "> 🎯 **Độ chính xác:** `50%`\n"
            "> 📊 **Hạng:** `#1`"
        ),
        "no_store": "❌ **Không có dữ liệu thống kê**",
        "board_en": "🏅 **Bảng xếp hạng nối từ: Tiếng Anh**\n🥇 <@2> — **1** thắng · 1 từ đúng",
        "board_title_en": "🏅 **Bảng xếp hạng nối từ: Tiếng Anh**\n",
        "board_title_vi": "🏅 **Bảng xếp hạng nối từ: Tiếng Việt**\n",
        "empty_en": "📭 **Chưa có ai chơi nối từ Tiếng Anh**\n> Hãy bắt đầu trò chơi và là người đầu tiên!",
        "empty_vi": "📭 **Chưa có ai chơi nối từ Tiếng Việt**\n> Hãy bắt đầu trò chơi và là người đầu tiên!",
        "unsupported": "❌ **Ngôn ngữ không hỗ trợ**\n> Hãy dùng `en` (Tiếng Anh) hoặc `vi` (Tiếng Việt).",
    },
}


class ManagerStatsEnUi(unittest.TestCase):
    ui = "en"

    def setUp(self) -> None:
        self.store = GameStore(":memory:")
        self.mgr = self.make_manager()
        self.t = EXPECTED[self.ui]

    def make_manager(self, store="default", default_language="vi") -> WordChainGameManager:
        return WordChainGameManager(
            default_language=default_language,
            ui_language=self.ui,
            store=self.store if store == "default" else store,
            vi_dictionary=make_vi(),
            en_dictionary=make_en(),
        )

    def set_active(self, language: str) -> None:
        if language == "en":
            state = WordChainState("apple", "e", "en", "e", {"apple": None})
        else:
            state = WordChainState("học sinh", "sinh", "vi", syllable_key("sinh"), {})
        self.mgr._sessions[CH] = ChannelSession(state)

    def seed_bob(self) -> None:
        self.store.bump(1, "correct", "vi")
        self.store.bump(1, "wins", "en")
        self.store.bump(1, "correct", "en", 2)
        self.store.bump(1, "wrong", "en", 2)

    # ---------------------------------------------------------- profile
    def test_profile_both_languages(self):
        self.seed_bob()
        self.assertEqual(
            self.mgr.player_profile(1, "Bob"),
            "\n".join([
                self.t["header"],
                self.t["vi_title"], self.t["vi_section"],
                self.t["en_title"], self.t["en_section"],
            ]),
        )

    def test_profile_only_english(self):
        self.store.bump(1, "wins", "en")
        self.store.bump(1, "correct", "en", 2)
        self.store.bump(1, "wrong", "en", 2)
        self.assertEqual(
            self.mgr.player_profile(1, "Bob"),
            "\n".join([
                self.t["header"],
                self.t["vi_title"], self.t["empty"],
                self.t["en_title"], self.t["en_section"],
            ]),
        )

    def test_profile_no_stats(self):
        self.assertEqual(
            self.mgr.player_profile(1, "Bob"),
            "\n".join([
                self.t["header"],
                self.t["vi_title"], self.t["empty"],
                self.t["en_title"], self.t["empty"],
            ]),
        )

    def test_profile_default_english_first(self):
        self.seed_bob()
        self.mgr = self.make_manager(default_language="en")
        self.assertEqual(
            self.mgr.player_profile(1, "Bob"),
            "\n".join([
                self.t["header"],
                self.t["en_title"], self.t["en_section"],
                self.t["vi_title"], self.t["vi_section"],
            ]),
        )

    def test_profile_no_store(self):
        self.mgr = self.make_manager(store=None)
        self.assertEqual(self.mgr.player_profile(1, "Bob"), self.t["no_store"])

    # ---------------------------------------------------------- leaderboard
    def seed_board(self) -> None:
        self.store.bump(2, "wins", "en")
        self.store.bump(2, "correct", "en")
        self.store.bump(3, "wins", "vi", 4)  # Vietnamese only

    def test_leaderboard_default_language(self):
        self.seed_board()
        self.assertTrue(self.mgr.leaderboard(channel_id=CH).startswith(self.t["board_title_vi"]))
        self.set_active("en")
        self.assertEqual(self.mgr.leaderboard(channel_id=CH), self.t["board_en"])
        self.assertNotIn("<@3>", self.mgr.leaderboard(channel_id=CH))
        self.set_active("vi")
        board = self.mgr.leaderboard(channel_id=CH)
        self.assertTrue(board.startswith(self.t["board_title_vi"]))
        self.assertIn("<@3>", board)
        self.assertNotIn("<@2>", board)

    def test_leaderboard_explicit_language(self):
        self.seed_board()
        for language in ("en", "English", " EN "):
            with self.subTest(language=language):
                self.assertEqual(self.mgr.leaderboard(language), self.t["board_en"])
        for language in (" VI ", "tiếng việt", "tieng viet", "Vietnamese"):
            with self.subTest(language=language):
                self.assertTrue(self.mgr.leaderboard(language).startswith(self.t["board_title_vi"]))
        # An explicit language wins over the running game's language.
        self.set_active("vi")
        self.assertEqual(self.mgr.leaderboard("en"), self.t["board_en"])
        self.set_active("en")
        self.assertTrue(self.mgr.leaderboard("vi").startswith(self.t["board_title_vi"]))

    def test_leaderboard_empty(self):
        self.assertEqual(self.mgr.leaderboard(), self.t["empty_vi"])
        self.assertEqual(self.mgr.leaderboard("en"), self.t["empty_en"])
        self.store.bump(3, "wins", "vi")
        self.assertEqual(self.mgr.leaderboard("en"), self.t["empty_en"])
        no_store = self.make_manager(store=None)
        self.assertEqual(no_store.leaderboard(), self.t["empty_vi"])
        self.assertEqual(no_store.leaderboard("en"), self.t["empty_en"])

    def test_leaderboard_invalid_language(self):
        self.seed_board()
        self.assertEqual(self.mgr.start_game(CH, "fr"), (False, self.t["unsupported"]))
        for language in ("fr", ""):
            with self.subTest(language=language):
                self.assertEqual(self.mgr.leaderboard(language), self.mgr.start_game(CH, "fr")[1])
                self.assertEqual(self.mgr.leaderboard(language), self.t["unsupported"])

    def test_leaderboard_limit(self):
        for user in (1, 2, 3):
            self.store.bump(user, "wins", "en", user)
        board = self.mgr.leaderboard("en", limit=2)
        lines = board.split("\n")
        self.assertEqual(len(lines), 3)  # title + 2 players
        self.assertIn("<@3>", lines[1])
        self.assertIn("<@2>", lines[2])
        self.assertNotIn("<@1>", board)


class ManagerStatsViUi(ManagerStatsEnUi):
    ui = "vi"


# ---------------------------------------------------------------- help
class HelpTextTests(unittest.TestCase):
    def test_help_length_and_rank_line(self):
        from bot import build_help_text

        for language in ("en", "vi"):
            for prefix in ("!", "wc!"):
                with self.subTest(language=language, prefix=prefix):
                    text = build_help_text(prefix, language)
                    self.assertLess(len(text), 1950)
                    self.assertIn("⚠️", text)
                    if language == "en":
                        self.assertIn(f"`{prefix}chainrank [en|vi]`", text)
                    else:
                        self.assertIn(f"`{prefix}bxh [en|vi]`", text)


# ---------------------------------------------------------------- bot reactions
class BotReactionTests(unittest.TestCase):
    def test_reaction_for(self):
        from bot import reaction_for
        from word_chain import PhraseStatus

        self.assertEqual(reaction_for(PhraseStatus.BANNED), "⚠️")
        self.assertEqual(reaction_for(PhraseStatus.OK), "✅")
        self.assertEqual(reaction_for(PhraseStatus.WIN), "✅")
        self.assertEqual(reaction_for(PhraseStatus.COOLDOWN), "⏳")
        for status in (
            PhraseStatus.INVALID, PhraseStatus.WRONG_START, PhraseStatus.NOT_IN_DICT, PhraseStatus.USED,
        ):
            with self.subTest(status=status):
                self.assertEqual(reaction_for(status), "❌")

    def test_reply_delete_after(self):
        from bot import ERROR_REPLY_DELETE_AFTER, reply_delete_after
        from word_chain import PhraseStatus

        self.assertIsNone(reply_delete_after(PhraseStatus.BANNED))
        self.assertEqual(ERROR_REPLY_DELETE_AFTER, 6.0)
        for status in (
            PhraseStatus.COOLDOWN, PhraseStatus.INVALID, PhraseStatus.WRONG_START,
            PhraseStatus.NOT_IN_DICT, PhraseStatus.USED,
        ):
            with self.subTest(status=status):
                self.assertEqual(reply_delete_after(status), 6.0)


if __name__ == "__main__":
    unittest.main()
