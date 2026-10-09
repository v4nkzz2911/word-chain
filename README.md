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
- Hint for the next word (5 per person per day, shared by both languages, resets at midnight Vietnam time):
  `/chainhint` (only you see it) or `<prefix>chainhint` (alias `<prefix>goiy`, `<prefix>hint`)
- Vote to skip a word nobody can continue: click the ⏭️ **Skip** button under the game messages, or use
  `/chainskip` / `<prefix>chainskip` (alias `<prefix>boqua`, `<prefix>skip`).
  When 2 different players vote, the bot reveals a possible answer and starts a new round (nobody wins).
  Votes reset when someone answers correctly.
- Player profile: `/chainme [member]` or `<prefix>chainme [member]` (alias `<prefix>hoso`). Shows English and Vietnamese stats separately, each with its own rank.
- Top 20 leaderboard, one per language: `/chainrank [language]` or `<prefix>chainrank [en|vi]` (aliases `<prefix>chaintop`, `<prefix>bxh`). Without a language it shows the running game's language, otherwise `DISCORD_BOT_LANGUAGE`.
- Check a word: `/chaincheck <word>` or `<prefix>chaincheck <word>` (alias `<prefix>kiemtra`)
- Add / remove a word (Manage Server permission): `/chainadd <word>`, `/chainremove <word>`
  (aliases `<prefix>them-tu` / `<prefix>themtu`, `<prefix>xoa-tu` / `<prefix>xoatu`).
  A single word of letters a–z (e.g. `zyzzyva`) goes to the English dictionary;
  a 2-syllable word (e.g. `học sinh`) goes to the Vietnamese dictionary.

`<prefix>` is your `DISCORD_COMMAND_PREFIX` value (default `!`).
Vietnamese aliases work only with the prefix; slash commands keep their English names.

## Word-chain mini game

- Only one word-chain game can run at a time.
- Run `!chainstart` and the bot sends a random starter word from dictionary.
- You can force language when starting: `!chainstart en` or `!chainstart vi`.
- If you do not pass a language, the game uses `DISCORD_BOT_LANGUAGE`.
- Players can answer several times in a row, but each user has a 5-second cooldown between accepted answers.
- The bot reacts ✅ (correct), ❌ (wrong), ⏳ (still on cooldown) or ⚠️ (offensive word, English games); error replies delete themselves after 6 seconds, but the ⚠️ warning stays.
- The active game and player stats are saved in `data/word_chain.db`, so a restart does not lose them.
- Stats (correct, wrong, wins) and ranks are kept separately for English and Vietnamese; the daily hint limit is shared. On the first start after this update, stats from older versions are copied once into the Vietnamese stats; the old `players` table is left untouched.

### Vietnamese (`vi`)

- Every answer is exactly **2 syllables** and must start with the last syllable of the previous word (same tone).
- Words are checked against about **53,100** two-syllable words from two sources, downloaded to
  `data/words*.txt` on first run:
  - [vietnamese-wordlist](https://github.com/duyet/vietnamese-wordlist): Viet74K + Viet22K + Viet11K
    (Viet39K is not used because all of its 2-syllable words are already in Viet74K).
  - [minhqnd/dictionary](https://github.com/minhqnd/dictionary): Vietnamese 2-syllable words that have a
    Vietnamese definition. The first run downloads its ~170 MB database once, extracts the words to
    `data/words_minhqnd.txt` and deletes the database, so the first start takes a few minutes.
  Extra words can be added in `data/extra_words.txt` (one per line) or with `/chainadd`.
- Both tone-mark styles are accepted: `hoà`/`hòa`, `thuỷ`/`thủy`, and both i/y spellings: `mỹ`/`mĩ`, `kỳ`/`kì`, `quý`/`quí`.
- Starter words are picked from common syllables (via `wordfreq`) so the first move is not too obscure.
- Messages that are not 2 syllables are treated as chat and ignored.
- Whoever plays a word that **nobody can continue** wins, and a new round starts automatically.
- Example flow:
	- Bot: `học sinh`
	- Player: `sinh viên`
	- Next valid word must start with `viên`.

### English (`en`)

- Every answer is **one English word** of 3+ letters (a–z) that starts with the **last letter** of the
  previous word. A word cannot be used twice in a round (the starter word counts as used).
- Markdown, trailing punctuation, capitals and accents are ignored: `**Apple!**` → `apple`, `café` → `cafe`.
- Messages with spaces, apostrophes, hyphens or digits (`don't`, `x-ray`, `abc1`, `hello there`) are treated
  as chat and ignored.
- Offensive words (slurs, swear words, sexual terms and their common forms) are banned. The bot reacts ⚠️
  and posts a warning to the player that is not auto-deleted. The word is not accepted, the chain does not
  move, the player's cooldown is not used, and it counts as a wrong answer in the English stats.
  `/chaincheck` reports such words as banned and `/chainadd` refuses them. Some harmless words that share
  a stem are refused too (e.g. `hell`, `cocked`, `craps`, `niggle`).
- Words are checked against the [ENABLE](https://github.com/dolph/dictionary) word list (~172,800 words,
  public domain), downloaded once to `data/words_en_enable.txt` on first run.
  Extra words can be added in `data/extra_words_en.txt` (one per line) or with `/chainadd`.
- Starter words are common 4–8 letter words (via `wordfreq`) that do not end in j, q, x, y or z.
- Hints show the first letter and the length, e.g. `a____`, plus how many words can follow.
- Whoever plays a word that **nobody can continue** (no unused word starts with its last letter) wins,
  and a new round starts automatically.
- Example flow: `apple` → `egg` → `goat` → `tiger` → ...
- If the word list cannot be downloaded, English games are unavailable but Vietnamese games still work.
- English games saved by the old phrase-based version resume from the last letter of the last phrase.

## Dictionary credits

- English: ENABLE word list (public domain), via [dolph/dictionary](https://github.com/dolph/dictionary).
- Word lists by Hồ Ngọc Đức, via [duyet/vietnamese-wordlist](https://github.com/duyet/vietnamese-wordlist) (GPL-2.0).
- Dictionary data by [@minhqnd](https://github.com/minhqnd), [dict.minhqnd.com](https://dict.minhqnd.com) (CC BY-SA 4.0).

## Notes

- If you do not set `DISCORD_GUILD_ID`, slash commands are synced globally and can take time to appear.
- Add your app ID later by setting `DISCORD_APP_ID` in `.env`.
- Enable **Message Content Intent** for your bot in the Discord Developer Portal, otherwise message-based game logic will not receive player text.
