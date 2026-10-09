"""SQLite storage: active game, per-language player stats, hints and custom dictionary edits."""
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS active_game (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    channel_id INTEGER NOT NULL,
    state      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    correct INTEGER NOT NULL DEFAULT 0,
    wrong   INTEGER NOT NULL DEFAULT 0,
    wins    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_stats (
    user_id  INTEGER NOT NULL,
    language TEXT NOT NULL,          -- 'en' or 'vi'
    correct  INTEGER NOT NULL DEFAULT 0,
    wrong    INTEGER NOT NULL DEFAULT 0,
    wins     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, language)
);
CREATE TABLE IF NOT EXISTS custom_words (
    word    TEXT PRIMARY KEY,
    added   INTEGER NOT NULL,  -- 1 = added, 0 = removed from dictionary
    by_user INTEGER
);
CREATE TABLE IF NOT EXISTS hint_usage (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,     -- YYYY-MM-DD in Vietnam time
    used    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""

_STAT_FIELDS = {"correct", "wrong", "wins"}
_LANGUAGES = {"en", "vi"}  # game languages with their own stats (kept here: no import of word_chain)
SCHEMA_VERSION = 1


class GameStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """v0 -> v1: copy the old combined stats (players) into player_stats as Vietnamese.

        The players table is kept as it was and is no longer written.
        """
        if self.conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if self.conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                self.conn.execute(
                    "INSERT OR IGNORE INTO player_stats (user_id, language, correct, wrong, wins) "
                    "SELECT user_id, 'vi', correct, wrong, wins FROM players"
                )
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    @staticmethod
    def _check_language(language: str) -> None:
        if language not in _LANGUAGES:
            raise ValueError(language)

    # ---------- active game ----------
    def save_game(self, channel_id: int, state_json: str) -> None:
        self.conn.execute(
            "INSERT INTO active_game (id, channel_id, state) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET channel_id = excluded.channel_id, state = excluded.state",
            (channel_id, state_json),
        )
        self.conn.commit()

    def load_game(self) -> tuple[int, str] | None:
        row = self.conn.execute("SELECT channel_id, state FROM active_game WHERE id = 1").fetchone()
        return (row["channel_id"], row["state"]) if row else None

    def delete_game(self) -> None:
        self.conn.execute("DELETE FROM active_game")
        self.conn.commit()

    # ---------- player stats ----------
    # Each game language ("en" / "vi") has its own stats and ranks.
    def bump(self, user_id: int, field: str, language: str, n: int = 1) -> None:
        if field not in _STAT_FIELDS:
            raise ValueError(field)
        self._check_language(language)
        self.conn.execute(
            f"INSERT INTO player_stats (user_id, language, {field}) VALUES (?, ?, ?) "
            f"ON CONFLICT(user_id, language) DO UPDATE SET {field} = {field} + excluded.{field}",
            (user_id, language, n),
        )
        self.conn.commit()

    def get_player(self, user_id: int, language: str) -> dict[str, int]:
        self._check_language(language)
        row = self.conn.execute(
            "SELECT correct, wrong, wins FROM player_stats WHERE user_id = ? AND language = ?",
            (user_id, language),
        ).fetchone()
        return dict(row) if row else {"correct": 0, "wrong": 0, "wins": 0}

    def top(self, language: str, limit: int = 20) -> list[sqlite3.Row]:
        self._check_language(language)
        return self.conn.execute(
            "SELECT user_id, correct, wrong, wins FROM player_stats WHERE language = ? "
            "ORDER BY wins DESC, correct DESC LIMIT ?",
            (language, limit),
        ).fetchall()

    def rank_of(self, user_id: int, language: str) -> int | None:
        player = self.get_player(user_id, language)
        if not (player["correct"] or player["wins"] or player["wrong"]):
            return None
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM player_stats "
            "WHERE language = ? AND (wins > ? OR (wins = ? AND correct > ?))",
            (language, player["wins"], player["wins"], player["correct"]),
        ).fetchone()
        return row["n"] + 1

    # ---------- hints ----------
    def hints_used(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT used FROM hint_usage WHERE user_id = ? AND day = ?", (user_id, day)
        ).fetchone()
        return row["used"] if row else 0

    def record_hint(self, user_id: int, day: str) -> None:
        self.conn.execute(
            "INSERT INTO hint_usage (user_id, day, used) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, day) DO UPDATE SET used = used + 1",
            (user_id, day),
        )
        # Older days are no longer needed.
        self.conn.execute("DELETE FROM hint_usage WHERE day < ?", (day,))
        self.conn.commit()

    # ---------- custom words ----------
    def set_custom_word(self, word: str, added: bool, by_user: int | None) -> None:
        # REPLACE = delete + insert -> new rowid, so the latest edit is applied last on reload.
        self.conn.execute(
            "INSERT OR REPLACE INTO custom_words VALUES (?, ?, ?)",
            (word, int(added), by_user),
        )
        self.conn.commit()

    def custom_words(self) -> list[tuple[str, bool]]:
        rows = self.conn.execute("SELECT word, added FROM custom_words ORDER BY rowid").fetchall()
        return [(row["word"], bool(row["added"])) for row in rows]
