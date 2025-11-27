"""
Docker-related commands for the bot.
"""
import discord
from discord.ext import commands
import os
import asyncio
import logging
import re
from typing import TYPE_CHECKING
from docker_utils import get_docker_client, get_ssh_client

if TYPE_CHECKING:
    from bot import PlexBot
    from docker.client import DockerClient
    from docker.models.containers import Container

logger = logging.getLogger(__name__)


class DockerCog(commands.Cog, name="Docker"):
    def __init__(self, bot: "PlexBot"):
        self.bot = bot

    @commands.hybrid_command(name="plexstatus", description="Check Plex Docker container status.")
    async def plex_status_command(self, ctx: commands.Context) -> None:
        """Checks the status of the Plex Docker container."""
        # Defer allows the bot to process for more than 3 seconds without timing out
        await ctx.defer(ephemeral=False)

        client: "DockerClient" = get_docker_client()
        if not client:
            # FIXED: Use ctx.send instead of ctx.send
            await ctx.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
            return

        try:
            plex_container: "Container" = client.containers.get("plex")
            status: str = plex_container.status
            # FIXED: Use ctx.send
            await ctx.send(f"🎬 Plex container status: `{status}`")
        except self.bot.docker.errors.NotFound:
            # FIXED: Use ctx.send
            await ctx.send("Plex container not found. Check your container name.", ephemeral=True)
        except Exception as e:
            logging.error(f"Error checking Plex status: {e}")
            # FIXED: Use ctx.send
            await ctx.send(f"An unexpected error occurred: {e}", ephemeral=True)

    @commands.hybrid_command(name="restartcontainers", description="Restarts the stack with clean log output")
    async def restart_containers_command(self, ctx: commands.Context) -> None:
        """Restarts the Docker stack and streams the logs."""
        await ctx.defer()

        def clean_ansi_codes(text: str) -> str:
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|[[0-?]*[- /]*[@-~])')
            return ansi_escape.sub('', text)

        embed = discord.Embed(title="🚀 Stack Restart Initiated",
                              description="Connecting to host...", color=discord.Color.blue())
        # FIXED: Use ctx.send. This returns a Message object we can edit later.
        msg = await ctx.send(embed=embed)

        script_path: str = os.getenv("STACK_RESTART_SCRIPT")
        if not script_path:
            embed.description = "❌ Error: `STACK_RESTART_SCRIPT` env var is missing."
            embed.color = discord.Color.red()
            await msg.edit(embed=embed)
            return

        ssh = await get_ssh_client()
        if not ssh:
            embed.description = "❌ Error: Could not connect to host via SSH."
            embed.color = discord.Color.red()
            await msg.edit(embed=embed)
            return

        try:
            command = f"{script_path} restart"
            stdin, stdout, stderr = await asyncio.to_thread(ssh.exec_command, command, get_pty=True)

            full_output = ""
            last_update_time = 0
            import time

            for line in iter(stdout.readline, ""):
                cleaned_line = clean_ansi_codes(line)
                full_output += cleaned_line

                current_time = time.time()
                if current_time - last_update_time > 2.0:
                    display_text = full_output[-1500:]

                    embed.description = f"**Executing: `stack restart`**\n```bash\n{display_text}\n```"
                    try:
                        await msg.edit(embed=embed)
                    except discord.HTTPException:
                        pass
                    last_update_time = current_time

            cleaned_final = clean_ansi_codes(full_output)[-1500:]
            embed.title = "✅ Stack Restart Complete"
            embed.description = f"**Execution Finished**\n```bash\n{cleaned_final}\n```"
            embed.color = discord.Color.green()
            embed.set_footer(text="Services should be stabilizing now.")
            await msg.edit(embed=embed)

        except Exception as e:
            logging.error(f"Stack restart exception: {e}")
            embed.title = "❌ Execution Error"
            embed.description = f"An error occurred:\n```\n{e}\n```"
            embed.color = discord.Color.red()
            await msg.edit(embed=embed)
        finally:
            ssh.close()

    @commands.hybrid_command(name="restartplex", description="Restart Plex container.")
    async def restart_plex_command(self, ctx: commands.Context) -> None:
        """Restarts the Plex Docker container."""
        await ctx.defer(ephemeral=False)

        client: "DockerClient" = get_docker_client()
        if not client:
            # FIXED: Use ctx.send
            await ctx.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
            return

        try:
            plex_container: "Container" = client.containers.get("plex")

            # FIXED: Use ctx.send
            await ctx.send("🔄 Restarting Plex container...", ephemeral=False)
            await asyncio.to_thread(plex_container.restart, timeout=30)
            await asyncio.sleep(5)
            plex_container.reload()
            status: str = plex_container.status
            # FIXED: Use ctx.send
            await ctx.send(f"✅ Plex container restart initiated. Current status: `{status}`")

        except self.bot.docker.errors.NotFound:
            # FIXED: Use ctx.send
            await ctx.send("Plex container not found. Check your container name.", ephemeral=True)
        except Exception as e:
            logging.error(f"Error restarting Plex: {e}")
            # FIXED: Use ctx.send
            await ctx.send(f"An unexpected error occurred: {e}", ephemeral=True)


async def setup(bot: "PlexBot") -> None:
    # Docker library is optional
    try:
        import docker
        bot.docker = docker
    except ImportError:
        logging.warning(
            "Docker library not found, Docker-related commands will be unavailable.")
        bot.docker = None

    if bot.docker:
        await bot.add_cog(DockerCog(bot))
    else:
        logging.warning(
            "Not loading DockerCog because the Docker library is not installed.")
