
"""
Real-Debrid related commands for the bot.
"""
import discord
from discord.ext import commands, tasks
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from realdebrid_utils import get_realdebrid_client

if TYPE_CHECKING:
    from bot import PlexBot

logger = logging.getLogger(__name__)


class RealDebridCog(commands.Cog, name="Real-Debrid"):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot
        self.check_premium_expiry.start()

    def cog_unload(self) -> None:
        """Cancels the premium expiry check task when the cog is unloaded."""
        self.check_premium_expiry.cancel()

    @tasks.loop(hours=24)
    async def check_premium_expiry(self) -> None:
        """Periodically checks Real-Debrid premium status and sends a notification if expiring soon."""
        await self.bot.wait_until_ready()

        channel_id_str = self.bot.config.discord.sonarr_notification_channel_id
        if not channel_id_str:
            logger.warning(
                "RealDebrid: Could not find notification channel id in config. Cannot send expiry notifications.")
            return

        try:
            channel_id = int(channel_id_str)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.warning(
                    f"RealDebrid: Could not find notification channel with ID: {channel_id}. Cannot send expiry notifications.")
                return
        except (ValueError, TypeError):
            logger.error(
                f"Invalid 'notification_channel_id' in config: {channel_id_str}. Must be a valid integer.")
            return

        data = await get_realdebrid_client()

        if data and data.get('type') == 'premium':
            premium_until_str = data.get('expiration')
            if premium_until_str:
                try:
                    expiry_date = datetime.fromisoformat(
                        premium_until_str.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    logger.error(
                        f"Invalid 'expiration' date format from Real-Debrid API: {premium_until_str}")
                    return

                today_utc = datetime.now(timezone.utc)
                time_difference = expiry_date - today_utc
                formatted_expiry_date = expiry_date.strftime("%B %d, %Y")

                logger.info(
                    f"RealDebrid: Premium expires on {formatted_expiry_date}, {time_difference.days} days left.")

                if 0 < time_difference.days <= 7:
                    logger.info(
                        f"RealDebrid: Sending premium expiry warning for {time_difference.days} days left.")
                    await channel.send(f"⚠️ Your Real-Debrid premium is expiring in **{time_difference.days} days**! (Expires: {formatted_expiry_date})")
                elif time_difference.days <= 0:
                    logger.warning(
                        "RealDebrid: Sending premium expired notification.")
                    await channel.send("🔴 Your Real-Debrid premium has expired! Please renew.")
                else:
                    logger.info(
                        f"RealDebrid: Premium is still far out ({time_difference.days} days). No notification sent.")
            else:
                logger.warning(
                    "RealDebrid: 'expiration' date not found in API response. Cannot determine expiry.")
        elif data:
            logger.info(
                "RealDebrid: Account is not premium or type is not specified. No expiry date to track.")
        else:
            await channel.send("Error: Could not retrieve Real-Debrid account information for expiry check.")

    @commands.hybrid_command(name="realdebrid", description="Check your Real-Debrid account status.")
    async def realdebrid_status_command(self, ctx: commands.Context) -> None:
        """Checks and displays the Real-Debrid account status."""
        logger.info(
            f"RealDebrid: Received /realdebrid command from user {ctx.author.id}.")
        await ctx.defer(ephemeral=False)

        data = await get_realdebrid_client()

        if not data:
            await ctx.send("Could not retrieve Real-Debrid account information.", ephemeral=True)
            return

        expiration_str = data.get('expiration')
        premium_status = data.get('type') == 'premium'

        embed = discord.Embed(
            title="Real-Debrid Account Status",
            color=discord.Color.green() if premium_status else discord.Color.red()
        )
        embed.add_field(name="Username", value=data.get(
            'username', 'N/A'), inline=True)
        embed.add_field(name="Email", value=data.get(
            'email', 'N/A'), inline=True)
        embed.add_field(name="Points", value=data.get(
            'points', 'N/A'), inline=True)

        if expiration_str:
            try:
                expiration_date = datetime.fromisoformat(
                    expiration_str.replace('Z', '+00:00'))
                today_utc = datetime.now(timezone.utc)
                days_left = (expiration_date - today_utc).days

                embed.add_field(
                    name="Premium Status", value="Active" if premium_status else "Expired", inline=True)
                embed.add_field(name="Expires In",
                                value=f"{days_left} days", inline=True)
                embed.set_footer(
                    text=f"Expires on {expiration_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")

            except (ValueError, TypeError):
                logger.warning(
                    f"RealDebrid: Invalid 'expiration' date format received for command: {expiration_str}")
                embed.add_field(name="Expiration Date",
                                value="N/A (Invalid Format)", inline=False)
        else:
            embed.add_field(name="Premium Status",
                            value="Not Premium", inline=True)
            embed.add_field(name="Expiration Date", value="N/A", inline=False)

        await ctx.send(embed=embed)
        logger.info(
            f"RealDebrid: Sent /realdebrid command response to user {ctx.author.id}.")


async def setup(bot: "PlexBot") -> None:
    await bot.add_cog(RealDebridCog(bot))
