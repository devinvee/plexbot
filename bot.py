"""
Main bot file for the Plex Discord Bot.
"""
import os
import logging
import asyncio
import json
import discord
from discord.ext import commands
from utils import load_config
from media_watcher_service import setup_media_watcher_service
from typing import Dict, Any

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
bot_config: Dict[str, Any] = {}
try:
    bot_config = load_config(CONFIG_FILE)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(
        f"Error loading configuration from '{CONFIG_FILE}': {e}. Exiting.")
    exit(1)

# --- Logging Setup ---
configured_log_level_str: str = bot_config.get("log_level", "INFO").upper()
LOGGING_LEVELS: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}
log_level: int = LOGGING_LEVELS.get(configured_log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format='%(asctime)s %(levelname)-8s %(name)-15s %(message)s',
    handlers=[logging.StreamHandler()],
    force=True
)

root_logger: logging.Logger = logging.getLogger()
effective_level_numeric: int = root_logger.getEffectiveLevel()
effective_level_name: str = logging.getLevelName(effective_level_numeric)
logging.critical(
    f"LOGGING SERVICE: Root logger initialized. Effective log level set to: {effective_level_name} (Numeric: {effective_level_numeric})")

if log_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
else:
    logging.getLogger('discord').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('paramiko').setLevel(logging.WARNING)


# --- Bot Setup ---
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logging.error(
        "DISCORD_TOKEN environment variable not set. Please add it to your .env file. Exiting.")
    exit(1)

intents: discord.Intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class PlexBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config: Dict[str, Any] = bot_config

    async def setup_hook(self) -> None:
        # Load cogs
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')
                logging.info(f"Loaded cog: {filename}")
        
        # Setup media watcher service
        await setup_media_watcher_service(self)
        logging.info("Media Watcher Service setup initiated.")

    async def on_ready(self) -> None:
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        
