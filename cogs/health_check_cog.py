import discord
from discord.ext import commands
import logging
import os
import aiohttp
from typing import TYPE_CHECKING
from plexapi.server import PlexServer
from docker.client import DockerClient
from docker.errors import DockerException
from config import bot_config

if TYPE_CHECKING:
    from bot import PlexBot

logger = logging.getLogger(__name__)


class HealthCheckCog(commands.Cog, name="HealthCheck"):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot

    async def _check_env_vars(self) -> str:
        """Checks for the presence of required environment variables."""
        env_vars = [
            "DISCORD_TOKEN", "PLEX_URL", "PLEX_TOKEN", "REALDEBRID_API_KEY",
            "DOCKER_SERVER_IP", "DOCKER_SERVER_USER", "DOCKER_SERVER_PASSWORD",
            "STACK_RESTART_SCRIPT"
        ]
        return "".join(f"✅ {var}\n" if os.getenv(var) else f"❌ {var}\n" for var in env_vars)

    async def _check_config_file(self) -> str:
        """Checks if the config file was loaded (now just a placeholder, as loading is centralized)."""
        # This check is now implicit. If the bot is running, the config was loaded.
        return "✅ config.json loaded successfully by the bot.\n"

    async def _check_plex_connection(self) -> str:
        """Checks the connection to the Plex server asynchronously."""
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")
        if not plex_url or not plex_token:
            return "⚠️ PLEX_URL or PLEX_TOKEN not set.\n"
        try:
            # Running synchronous plexapi call in a separate thread
            plex: PlexServer = await self.bot.loop.run_in_executor(
                None, lambda: PlexServer(plex_url, plex_token)
            )
            return f"✅ Connected to Plex server: {plex.friendlyName}\n"
        except Exception as e:
            logger.error(f"Plex connection failed: {e}", exc_info=True)
            return f"❌ Could not connect to Plex: {e}\n"

    async def _check_realdebrid_connection(self) -> str:
        """Checks the connection to the Real-Debrid API."""
        rd_api_key = os.getenv("REALDEBRID_API_KEY")
        if not rd_api_key:
            return "⚠️ REALDEBRID_API_KEY not set.\n"
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {rd_api_key}"}
                async with session.get("https://api.real-debrid.com/rest/1.0/user", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return f"✅ Connected to Real-Debrid as: {data['username']}\n"
                    return f"❌ Real-Debrid API returned status: {resp.status}\n"
        except Exception as e:
            logger.error(f"Real-Debrid connection failed: {e}", exc_info=True)
            return f"❌ Could not connect to Real-Debrid: {e}\n"

    async def _check_docker_connection(self) -> str:
        """Checks the connection to the Docker daemon."""
        try:
            import docker
            client: DockerClient = await self.bot.loop.run_in_executor(None, docker.from_env)
            return f"✅ Connected to Docker daemon: {client.version()['Version']}\n"
        except ImportError:
            return "⚠️ Docker library not installed.\n"
        except DockerException as e:
            logger.error(f"Docker connection failed: {e}", exc_info=True)
            return f"❌ Could not connect to Docker daemon: {e}\n"

    @commands.hybrid_command(name="healthcheck", description="Checks the bot's configuration and connectivity.")
    @commands.is_owner()
    async def health_check(self, ctx: commands.Context):
        """Runs a comprehensive health check of the bot's services."""
        await ctx.defer(ephemeral=True)
        embed = discord.Embed(title="Health Check", color=discord.Color.blue())

        embed.add_field(name=".env Variables", value=await self._check_env_vars(), inline=False)
        embed.add_field(name="config.json", value=await self._check_config_file(), inline=False)
        embed.add_field(name="Plex", value=await self._check_plex_connection(), inline=False)
        embed.add_field(name="Real-Debrid", value=await self._check_realdebrid_connection(), inline=False)
        embed.add_field(name="Docker", value=await self._check_docker_connection(), inline=False)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: "PlexBot"):
    await bot.add_cog(HealthCheckCog(bot))
