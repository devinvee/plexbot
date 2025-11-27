"""
Plex-related commands for the bot.
"""
import discord
from discord.ext import commands
import logging
from typing import List, TYPE_CHECKING
from plex_utils import get_plex_client

if TYPE_CHECKING:
    from bot import PlexBot
    from plexapi.library import LibrarySection

logger = logging.getLogger(__name__)

class LibrarySelectView(discord.ui.View):
    def __init__(self, libraries: List["LibrarySection"]):
        super().__init__(timeout=300)

        select_options = [
            discord.SelectOption(label=lib.title) for lib in libraries
        ]

        self.add_item(discord.ui.Select(
            placeholder="Choose the libraries you're interested in...",
            options=select_options,
            min_values=1,
            max_values=len(libraries),
            custom_id="library_select"
        ))

    @discord.ui.select(custom_id="library_select")
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Callback for the library selection dropdown."""
        selected_libraries: List[str] = select.values

        await interaction.response.send_message(f"Thanks! I've noted your interest in: **{', '.join(selected_libraries)}**.", ephemeral=True)


class PlexCog(commands.Cog, name="Plex"):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot

    @commands.hybrid_command(name="plexaccess", description="Select which Plex libraries you are interested in.")
    async def plexaccess_command(self, ctx: commands.Context) -> None:
        """Allows a user to select which Plex libraries they are interested in."""
        await ctx.defer(ephemeral=False)
        try:
            plex = await self.bot.loop.run_in_executor(None, get_plex_client)

            if not plex:
                await ctx.followup.send("Plex server is not configured correctly. Please contact the admin.", ephemeral=True)
                return

            libraries: List["LibrarySection"] = await self.bot.loop.run_in_executor(None, plex.library.sections)

            if not libraries:
                await ctx.followup.send("No Plex libraries found.", ephemeral=True)
                return

            view = LibrarySelectView(libraries)

            await ctx.followup.send(
                "Awesome! So you'd like Plex access? Which libraries are you interested in?",
                view=view
            )

        except Exception as e:
            logging.error(f"Failed to execute /plexaccess command: {e}", exc_info=True)
            await ctx.followup.send(f"An error occurred while fetching Plex libraries. Please try again later.", ephemeral=True)

async def setup(bot: "PlexBot") -> None:
    await bot.add_cog(PlexCog(bot))
