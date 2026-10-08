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

`<prefix>` is your `DISCORD_COMMAND_PREFIX` value (default `!`).

## Word-chain mini game

- Only one word-chain game can run at a time.
- Run `!chainstart` and the bot sends a random starter word from dictionary.
- You can force language when starting: `!chainstart en` or `!chainstart vi`.
- If you do not pass a language, the game uses `DISCORD_BOT_LANGUAGE`.
- Players reply with a phrase whose first word matches the last word of the previous phrase.
- Player words are checked against the dictionary of the active game language.
- Each user has a 5-second cooldown between accepted answers.
- Example flow:
	- Bot: `chào cờ`
	- Player: `cờ vua`
	- Next valid phrase must start with `vua`.

## Notes

- If you do not set `DISCORD_GUILD_ID`, slash commands are synced globally and can take time to appear.
- Add your app ID later by setting `DISCORD_APP_ID` in `.env`.
- Enable **Message Content Intent** for your bot in the Discord Developer Portal, otherwise message-based game logic will not receive player text.
