# HQS Discord Bot (Python)

A starter Discord bot built with `discord.py`.

## 1) Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 2) Install dependencies

```powershell
pip install -r requirements.txt
```

## 3) Configure environment variables

Copy `.env.example` to `.env` and fill values:

- `DISCORD_BOT_TOKEN` (required)
- `DISCORD_APP_ID` (optional now, add when available)
- `DISCORD_GUILD_ID` (optional, for fast command sync in one server)
- `DISCORD_COMMAND_PREFIX` (optional, default is `!`)
- `DISCORD_BOT_LANGUAGE` (optional, `en` or `vi`, default is `en`)

## 4) Run the bot

```powershell
python src/bot.py
```

## Commands

- Help: `/help` or `<prefix>help`
- Health check: `/ping`
- Hello: `<prefix>hello`
- Start word-chain game: `/chainstart` or `<prefix>chainstart [en|vi]`
- Stop word-chain game: `/chainstop` or `<prefix>chainstop`
- Show word-chain status: `/chainstatus` or `<prefix>chainstatus`
- Player profile: `/chainme [member]` or `<prefix>chainme [member]` (alias `<prefix>hoso`)
- Top 20 leaderboard: `/chainrank` or `<prefix>chainrank` (alias `<prefix>bxh`)
- Check a word: `/chaincheck <word>` or `<prefix>chaincheck <word>` (alias `<prefix>kiemtra`)
- Add / remove a Vietnamese word (Manage Server permission): `/chainadd <word>`, `/chainremove <word>`
  (aliases `<prefix>them-tu` / `<prefix>themtu`, `<prefix>xoa-tu` / `<prefix>xoatu`)

`<prefix>` is your `DISCORD_COMMAND_PREFIX` value (default `!`).
Vietnamese aliases work only with the prefix; slash commands keep their English names.

## Word-chain mini game

- Only one word-chain game can run at a time.
- Run `!chainstart` and the bot sends a random starter word from dictionary.
- You can force language when starting: `!chainstart en` or `!chainstart vi`.
- If you do not pass a language, the game uses `DISCORD_BOT_LANGUAGE`.
- A player cannot answer twice in a row, and each user has a 5-second cooldown between accepted answers.
- The bot reacts ✅ (correct), ❌ (wrong) or ⏳ (not your turn yet); error replies delete themselves after 6 seconds.
- The active game and player stats are saved in `data/word_chain.db`, so a restart does not lose them.

### Vietnamese (`vi`)

- Every answer is exactly **2 syllables** and must start with the last syllable of the previous word (same tone).
- Words are checked against the [Viet74K](https://github.com/duyet/vietnamese-wordlist) word list, downloaded to `data/words.txt` on first run.
  Extra words can be added in `data/extra_words.txt` (one per line) or with `/chainadd`.
- Both tone-mark styles are accepted: `hoà`/`hòa`, `thuỷ`/`thủy`.
- Starter words are picked from common syllables (via `wordfreq`) so the first move is not too obscure.
- Messages that are not 2 syllables are treated as chat and ignored.
- Whoever plays a word that **nobody can continue** wins, and a new round starts automatically.
- Example flow:
	- Bot: `học sinh`
	- Player: `sinh viên`
	- Next valid word must start with `viên`.

### English (`en`)

- Players reply with a phrase (2+ words) whose first word matches the last word of the previous phrase.
- Each word is checked against the `wordfreq` English dictionary.

## Notes

- If you do not set `DISCORD_GUILD_ID`, slash commands are synced globally and can take time to appear.
- Add your app ID later by setting `DISCORD_APP_ID` in `.env`.
- Enable **Message Content Intent** for your bot in the Discord Developer Portal, otherwise message-based game logic will not receive player text.
