import logging
import discord
from discord.ext import commands
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot import PlexBot

class EventsCog(commands.Cog):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Event that fires when a user's server profile is updated,
        including when they are assigned a new role.
        """
        invite_config = self.bot.config.new_user_invite
        if not invite_config.enabled:
            return

        if not invite_config.role_id or not invite_config.invite_link:
            logging.warning("New user invite feature is enabled, but role_id or invite_link is missing.")
            return

        if before.roles == after.roles:
            return

        try:
            target_role = after.guild.get_role(invite_config.role_id)
        except (ValueError, TypeError):
            logging.error(f"Invalid 'role_id' for new user invite: {invite_config.role_id}.")
            return

        if not target_role:
            logging.warning(f"Could not find role with ID {invite_config.role_id} in {after.guild.name}.")
            return

        if target_role not in before.roles and target_role in after.roles:
            logging.info(f"User '{after.display_name}' assigned '{target_role.name}'. Sending invite.")
            message = (
                f"Hello {after.display_name}!\n\n"
                f"Welcome! As you've been assigned the '{target_role.name}' role, here is your invite link:\n"
                f"{invite_config.invite_link}"
            )
            try:
                await after.send(message)
                logging.info(f"Successfully sent invite DM to '{after.display_name}'.")
            except discord.Forbidden:
                logging.warning(f"Could not send DM to '{after.display_name}'. DMs may be disabled.")
            except Exception as e:
                logging.error(f"Error sending DM to '{after.display_name}': {e}", exc_info=True)

async def setup(bot: "PlexBot"):
    await bot.add_cog(EventsCog(bot))