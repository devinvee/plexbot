
"""
Admin-only commands for the bot.
"""
import discord
from discord.ext import commands
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot import PlexBot

logger = logging.getLogger(__name__)

class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context) -> None:
        """Manually syncs slash commands to the current guild."""
        try:
            await ctx.send("Attempting to sync commands to this server...")

            self.bot.tree.copy_global_to(guild=ctx.guild)

            synced = await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ Successfully synced **{len(synced)}** commands.")
        except Exception as e:
            await ctx.send(f"❌ Failed to sync commands: {e}")

async def setup(bot: "PlexBot") -> None:
    await bot.add_cog(AdminCog(bot))

