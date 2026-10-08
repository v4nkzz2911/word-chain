import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import load_config
from storage import GameStore
from word_chain import PhraseStatus, WordChainGameManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("hqs-bot")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ERROR_REPLY_DELETE_AFTER = 6.0


async def safe_react(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


def build_bot() -> tuple[commands.Bot, str]:
    config = load_config()

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix=config.command_prefix,
        intents=intents,
        application_id=config.app_id,
        help_command=None,
    )
    is_vi = config.bot_language == "vi"

    def tr(en: str, vi: str) -> str:
        return vi if is_vi else en

    word_chain = WordChainGameManager(
        default_language=config.bot_language,
        ui_language=config.bot_language,
        store=GameStore(DATA_DIR / "word_chain.db"),
    )

    def build_help_text() -> str:
        prefix = config.command_prefix
        if config.bot_language == "vi":
            return (
                "## **HQS Bot Help**\n"
                "> **Ngôn ngữ cấu hình hiện tại:** `Tiếng Việt (vi)`\n\n"
                "### **Lệnh chung**\n"
                f"- `{prefix}help` - Hiển thị hướng dẫn\n"
                f"- `{prefix}hello` - Trả lời lời chào\n"
                "- `/ping` - Kiểm tra bot còn hoạt động\n"
                "- `/help` - Hiển thị hướng dẫn\n\n"
                "### **Trò chơi Nối Từ**\n"
                f"- `{prefix}chainstart [en|vi]` hoặc `/chainstart` - Bắt đầu trò chơi\n"
                f"- `{prefix}chainstop` hoặc `/chainstop` - Dừng trò chơi\n"
                f"- `{prefix}chainstatus` hoặc `/chainstatus` - Xem trạng thái trò chơi\n"
                f"- `{prefix}goiy` hoặc `/chainhint` - Gợi ý từ tiếp theo (5 lần/ngày, `/chainhint` chỉ mình bạn thấy)\n"
                f"- `{prefix}hoso [người]` hoặc `/chainme` - Xem hồ sơ nối từ\n"
                f"- `{prefix}bxh` hoặc `/chainrank` - Bảng xếp hạng top 20\n"
                f"- `{prefix}kiemtra <từ>` hoặc `/chaincheck` - Kiểm tra từ có trong từ điển\n"
                f"- `{prefix}them-tu <từ>` / `{prefix}xoa-tu <từ>` - Thêm/xoá từ tiếng Việt (quản trị)\n\n"
                "### **Cách chơi (Tiếng Việt)**\n"
                f"1. Dùng `{prefix}chainstart vi` hoặc `/chainstart`\n"
                "2. Bot đưa ra một từ gồm 2 âm tiết, ví dụ `học sinh`\n"
                "3. Người chơi gửi một từ 2 âm tiết bắt đầu bằng âm tiết cuối (đúng cả dấu): `sinh viên` → `viên chức`\n"
                "4. Ai nối đến từ mà **không còn từ nào nối tiếp được** sẽ thắng 🏆, bot tự mở lượt mới\n\n"
                "### **Cách chơi (Tiếng Anh)**\n"
                "- Gửi cụm từ (từ 2 từ trở lên) bắt đầu bằng từ cuối của cụm trước: `king` → `king maker` → `maker ...`\n\n"
                "### **Quy tắc**\n"
                "- Từ phải có trong từ điển và chưa được dùng trong lượt chơi\n"
                "- Chấp nhận cả hai kiểu bỏ dấu: `hoà`/`hòa`, `thuỷ`/`thủy`, và cả i/y: `mỹ`/`mĩ`, `kỳ`/`kì`\n"
                "- Mỗi người chơi có cooldown 5 giây giữa hai lần trả lời hợp lệ\n"
                "- Tin nhắn không phải từ 2 âm tiết được coi là trò chuyện và bị bỏ qua (Tiếng Việt)\n"
                "- Chỉ được chạy 1 trò chơi nối từ tại một thời điểm\n"
                "- Bot thả ✅ khi đúng, ❌ khi sai, ⏳ khi đang trong cooldown\n\n"
                "-# Nguồn từ điển: Hồ Ngọc Đức (vietnamese-wordlist) · dữ liệu từ điển của @minhqnd, "
                "<https://dict.minhqnd.com> (CC BY-SA 4.0)"
            )

        return (
            "## **HQS Bot Help**\n"
            "> **Configured language:** `English (en)`\n\n"
            "### **General**\n"
            f"- `{prefix}help` - Show this help message\n"
            f"- `{prefix}hello` - Quick hello response\n"
            "- `/ping` - Check if the bot is alive\n"
            "- `/help` - Show this help message\n\n"
            "### **Word-chain Game**\n"
            f"- `{prefix}chainstart [en|vi]` or `/chainstart` - Start a new game in this channel\n"
            f"- `{prefix}chainstop` or `/chainstop` - Stop the current game in this channel\n"
            f"- `{prefix}chainstatus` or `/chainstatus` - Show game status in this channel\n"
            f"- `{prefix}chainhint` or `/chainhint` - Hint for the next word (Vietnamese games, 5 per day)\n"
            f"- `{prefix}chainme [member]` or `/chainme` - Show a word-chain profile\n"
            f"- `{prefix}chainrank` or `/chainrank` - Top 20 leaderboard\n"
            f"- `{prefix}chaincheck <word>` or `/chaincheck` - Check a word against the dictionary\n"
            f"- `{prefix}chainadd <word>` / `{prefix}chainremove <word>` - Edit the Vietnamese dictionary (admin)\n\n"
            "### **How To Play (English)**\n"
            f"1. Run `{prefix}chainstart [en|vi]` or `/chainstart`\n"
            "2. Bot gives a random starter word from the dictionary in the selected language\n"
            "3. Send a phrase that starts with the last word of the previous phrase\n"
            "4. Example: bot says `king` -> player says `king maker` -> next phrase must start with `maker`\n\n"
            "### **How To Play (Vietnamese)**\n"
            "- Each answer is exactly 2 syllables and starts with the last syllable (same tone): `học sinh` -> `sinh viên`\n"
            "- Whoever plays a word that **nobody can continue** wins 🏆 and a new round starts\n\n"
            "### **Rules**\n"
            "- Your first word must match the expected start word\n"
            "- Words are validated against the current game language dictionary and can't be reused\n"
            "- Cooldown: each user must wait 5 seconds between accepted answers\n"
            "- Only one word-chain game can run at a time\n"
            "- The bot reacts ✅ for correct, ❌ for wrong, ⏳ while you're on cooldown\n\n"
            "-# Vietnamese dictionary: Hồ Ngọc Đức (vietnamese-wordlist) · dictionary data by @minhqnd, "
            "<https://dict.minhqnd.com> (CC BY-SA 4.0)"
        )

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")

    @bot.event
    async def setup_hook() -> None:
        try:
            await asyncio.to_thread(word_chain.load_vietnamese_word_files, DATA_DIR)
            word_chain.apply_custom_words()
            logger.info(
                "Loaded %d Vietnamese 2-syllable words (%d starter words)",
                len(word_chain.vi_dictionary),
                word_chain.vi_dictionary.start_pool_size,
            )
        except Exception:
            logger.exception("Failed to load the Vietnamese dictionary; Vietnamese games are unavailable")

        if config.guild_id:
            guild = discord.Object(id=config.guild_id)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", config.guild_id)
        else:
            await bot.tree.sync()
            logger.info("Synced global commands")

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            text = tr(
                "❌ You need the **Manage Server** permission to use this command.",
                "❌ Bạn cần quyền **Quản lý máy chủ** để dùng lệnh này.",
            )
        elif isinstance(error, commands.NoPrivateMessage):
            text = tr("❌ This command can only be used in a server.", "❌ Lệnh này chỉ dùng trong server.")
        elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            text = tr(
                f"❌ Missing or invalid argument. Use `{config.command_prefix}help` for usage.",
                f"❌ Thiếu hoặc sai tham số. Gõ `{config.command_prefix}help` để xem hướng dẫn.",
            )
        else:
            logger.exception("Command %s failed", ctx.command, exc_info=error)
            text = tr("❌ Something went wrong.", "❌ Đã có lỗi xảy ra.")
        try:
            await ctx.send(text)
        except discord.HTTPException:
            pass

    @bot.tree.command(name="ping", description="Check if the bot is alive")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(tr("🏓 **Pong**", "🏓 **Pong**\n> Bot đang hoạt động"))

    @bot.tree.command(name="help", description="Show bot help and instructions")
    async def slash_help(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(build_help_text())

    @bot.command(name="hello")
    async def hello(ctx: commands.Context) -> None:
        await ctx.send(tr("👋 **Hello from HQS Bot**", "👋 **Xin chào từ HQS Bot**"))

    @bot.command(name="help")
    async def help_command(ctx: commands.Context) -> None:
        await ctx.send(build_help_text())

    @bot.command(name="chainstart")
    async def chain_start(ctx: commands.Context, language: str | None = None) -> None:
        _, response = word_chain.start_game(ctx.channel.id, language)
        await ctx.send(response)

    @bot.tree.command(name="chainstart", description="Start a word-chain game in this channel")
    @app_commands.describe(language="Optional language: en (English) or vi (Vietnamese)")
    @app_commands.choices(
        language=[
            app_commands.Choice(name="English", value="en"),
            app_commands.Choice(name="Vietnamese", value="vi"),
        ]
    )
    async def slash_chain_start(
        interaction: discord.Interaction,
        language: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.channel is None:
            await interaction.response.send_message(
                tr(
                    "This command must be used in a channel.",
                    "Lệnh này chỉ có thể dùng trong kênh.",
                )
            )
            return

        selected_language = language.value if language is not None else None
        _, response = word_chain.start_game(interaction.channel.id, selected_language)
        await interaction.response.send_message(response)

    @bot.command(name="chainstop")
    async def chain_stop(ctx: commands.Context) -> None:
        _, response = word_chain.stop_game(ctx.channel.id)
        await ctx.send(response)

    @bot.tree.command(name="chainstop", description="Stop the word-chain game in this channel")
    async def slash_chain_stop(interaction: discord.Interaction) -> None:
        if interaction.channel is None:
            await interaction.response.send_message(
                tr(
                    "This command must be used in a channel.",
                    "Lệnh này chỉ có thể dùng trong kênh.",
                )
            )
            return

        _, response = word_chain.stop_game(interaction.channel.id)
        await interaction.response.send_message(response)

    @bot.command(name="chainstatus")
    async def chain_status(ctx: commands.Context) -> None:
        _, response = word_chain.game_status(ctx.channel.id)
        await ctx.send(response)

    @bot.tree.command(name="chainstatus", description="Show word-chain status in this channel")
    async def slash_chain_status(interaction: discord.Interaction) -> None:
        if interaction.channel is None:
            await interaction.response.send_message(
                tr(
                    "This command must be used in a channel.",
                    "Lệnh này chỉ có thể dùng trong kênh.",
                )
            )
            return

        _, response = word_chain.game_status(interaction.channel.id)
        await interaction.response.send_message(response)

    @bot.command(name="chainhint", aliases=["goiy", "hint"])
    async def chain_hint(ctx: commands.Context) -> None:
        _, response = word_chain.give_hint(ctx.channel.id, ctx.author.id)
        await ctx.reply(response, mention_author=False)

    @bot.tree.command(name="chainhint", description="Get a hint for the next word (5 per day)")
    async def slash_chain_hint(interaction: discord.Interaction) -> None:
        if interaction.channel is None:
            await interaction.response.send_message(
                tr(
                    "This command must be used in a channel.",
                    "Lệnh này chỉ có thể dùng trong kênh.",
                ),
                ephemeral=True,
            )
            return

        _, response = word_chain.give_hint(interaction.channel.id, interaction.user.id)
        # Only the player who asked sees the hint.
        await interaction.response.send_message(response, ephemeral=True)

    @bot.command(name="chainme", aliases=["chainprofile", "hoso"])
    async def chain_me(ctx: commands.Context, member: discord.Member | None = None) -> None:
        member = member or ctx.author
        await ctx.send(word_chain.player_profile(member.id, member.display_name))

    @bot.tree.command(name="chainme", description="Show a word-chain profile")
    @app_commands.describe(member="Member to show (default: you)")
    async def slash_chain_me(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        await interaction.response.send_message(word_chain.player_profile(target.id, target.display_name))

    @bot.command(name="chainrank", aliases=["chaintop", "bxh"])
    async def chain_rank(ctx: commands.Context) -> None:
        await ctx.send(word_chain.leaderboard(), allowed_mentions=discord.AllowedMentions.none())

    @bot.tree.command(name="chainrank", description="Show the word-chain top 20 leaderboard")
    async def slash_chain_rank(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            word_chain.leaderboard(), allowed_mentions=discord.AllowedMentions.none()
        )

    @bot.command(name="chaincheck", aliases=["kiemtra"])
    async def chain_check(ctx: commands.Context, *, word: str) -> None:
        _, response = word_chain.check_word(word)
        await ctx.send(response)

    @bot.tree.command(name="chaincheck", description="Check whether a word is in the dictionary")
    @app_commands.describe(word="Word to check (Vietnamese: exactly 2 syllables)")
    async def slash_chain_check(interaction: discord.Interaction, word: str) -> None:
        _, response = word_chain.check_word(word)
        await interaction.response.send_message(response)

    @bot.command(name="chainadd", aliases=["themtu", "them-tu"])
    @commands.has_guild_permissions(manage_guild=True)
    async def chain_add(ctx: commands.Context, *, word: str) -> None:
        _, response = word_chain.add_word(word, ctx.author.id)
        await ctx.send(response)

    @bot.tree.command(name="chainadd", description="Add a Vietnamese word to the dictionary (admin)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(word="Vietnamese word with exactly 2 syllables")
    async def slash_chain_add(interaction: discord.Interaction, word: str) -> None:
        _, response = word_chain.add_word(word, interaction.user.id)
        await interaction.response.send_message(response)

    @bot.command(name="chainremove", aliases=["xoatu", "xoa-tu"])
    @commands.has_guild_permissions(manage_guild=True)
    async def chain_remove(ctx: commands.Context, *, word: str) -> None:
        _, response = word_chain.remove_word(word, ctx.author.id)
        await ctx.send(response)

    @bot.tree.command(name="chainremove", description="Remove a Vietnamese word from the dictionary (admin)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(word="Vietnamese word with exactly 2 syllables")
    async def slash_chain_remove(interaction: discord.Interaction, word: str) -> None:
        _, response = word_chain.remove_word(word, interaction.user.id)
        await interaction.response.send_message(response)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        command_context = await bot.get_context(message)
        if command_context.valid:
            await bot.process_commands(message)
            return

        result = word_chain.handle_player_phrase(
            message.channel.id,
            message.author.id,
            message.author.display_name,
            message.content,
        )
        if result is None:
            return

        if result.status == PhraseStatus.OK:
            await safe_react(message, "✅")
            return

        if result.status == PhraseStatus.WIN:
            await safe_react(message, "✅")
            await message.channel.send(result.message)
            return

        await safe_react(message, "⏳" if result.status == PhraseStatus.COOLDOWN else "❌")
        try:
            await message.reply(
                result.message,
                delete_after=ERROR_REPLY_DELETE_AFTER,
                mention_author=False,
            )
        except discord.HTTPException:
            pass

    return bot, config.token


def main() -> None:
    bot, token = build_bot()
    bot.run(token)


if __name__ == "__main__":
    main()
