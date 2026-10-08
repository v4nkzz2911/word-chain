import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import load_config
from word_chain import WordChainGameManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("hqs-bot")


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
                f"- `{prefix}chainstatus` hoặc `/chainstatus` - Xem trạng thái trò chơi\n\n"
                "### **Cách chơi**\n"
                f"1. Dùng `{prefix}chainstart [en|vi]` hoặc `/chainstart`\n"
                "2. Bot đưa ra một từ bắt đầu ngẫu nhiên theo ngôn ngữ đã chọn\n"
                "3. Người chơi gửi cụm từ mới bắt đầu bằng từ cuối của cụm trước\n"
                "4. Ví dụ: bot `vua` -> người chơi `vua cờ` -> lượt tiếp theo phải bắt đầu bằng `cờ`\n\n"
                "### **Quy tắc**\n"
                "- Từ đầu tiên phải đúng với từ được yêu cầu\n"
                "- Các từ sẽ được đối chiếu trong từ điển theo ngôn ngữ đang chơi\n"
                "- Chỉ được chạy 1 trò chơi nối từ tại một thời điểm\n"
                "- Mỗi người chơi có cooldown 5 giây giữa hai lần trả lời hợp lệ"
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
            f"- `{prefix}chainstatus` or `/chainstatus` - Show game status in this channel\n\n"
            "### **How To Play**\n"
            f"1. Run `{prefix}chainstart [en|vi]` or `/chainstart`\n"
            "2. Bot gives a random starter word from the dictionary in the selected language\n"
            "3. Send a phrase that starts with the last word of the previous phrase\n"
            "4. Example: bot says `king` -> player says `king maker` -> next phrase must start with `maker`\n\n"
            "### **Rules**\n"
            "- Your first word must match the expected start word\n"
            "- Words are validated against the current game language dictionary\n"
            "- Only one word-chain game can run at a time\n"
            "- Cooldown: each user must wait 5 seconds between accepted answers"
        )

    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")

    @bot.event
    async def setup_hook() -> None:
        if config.guild_id:
            guild = discord.Object(id=config.guild_id)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", config.guild_id)
        else:
            await bot.tree.sync()
            logger.info("Synced global commands")

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

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        command_context = await bot.get_context(message)
        if command_context.valid:
            await bot.process_commands(message)
            return

        response = word_chain.handle_player_phrase(
            message.channel.id,
            message.author.id,
            message.author.display_name,
            message.content,
        )
        if response is not None:
            _, text = response
            await message.channel.send(text)

        await bot.process_commands(message)

    return bot, config.token


def main() -> None:
    bot, token = build_bot()
    bot.run(token)


if __name__ == "__main__":
    main()
