from realdebrid_functions import realdebrid_status_command
from realdebrid_functions import check_premium_expiry as realdebrid_check_premium_expiry_task
from docker_functions import (
    restart_containers_logic,  # This is the core logic function
    restart_plex_command,
    restart_containers_command,  # This function uses RESTART_ORDER
    plex_status_command  # Don't forget to import this for the /plex_status command
)
from media_watcher_service import setup_media_watcher_service
import os
import logging
import asyncio
import json
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import requests

# Import the shared utility function to load configuration
from utils import load_config, load_dotenv

# --- Initial Environment Variable Loading ---
load_dotenv()

# --- Logging Setup (EARLY AND PRIMARY CONFIGURATION) ---
configured_log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()

LOGGING_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

numeric_level = LOGGING_LEVELS.get(configured_log_level_str, logging.INFO)

logging.basicConfig(
    level=numeric_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

if numeric_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
    logging.getLogger('discord.http').setLevel(logging.INFO)
else:
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info(f"Main bot logging level set to: {configured_log_level_str}")


# Import the media watcher service setup function

# Import docker library for Docker interaction
try:
    import docker
except ImportError:
    logger.error(
        "The 'docker' library is not installed. Docker commands will not work.", exc_info=True)
    docker = None


# --- Configuration Loading ---
CONFIG_FILE = "config.json"
config = {}
try:
    config = load_config(CONFIG_FILE)
    logger.info("Configuration loaded successfully.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.critical(
        f"Error loading configuration from '{CONFIG_FILE}': {e}. Exiting.", exc_info=True)
    exit(1)

# --- Extract configurations from the loaded config ---
DISCORD_TOKEN = config.get("discord_token", os.getenv("DISCORD_TOKEN"))
REALDEBRID_API_KEY = config.get(
    "realdebrid_api_key", os.getenv("REALDEBRID_API_KEY"))
CHANNEL_ID = config["discord"].get("notification_channel_id")
CHANNEL_ID_INT = int(
    CHANNEL_ID) if CHANNEL_ID and CHANNEL_ID.isdigit() else None

DOCKER_SERVER_IP = config.get("docker", {}).get(
    "server_ip", os.getenv("DOCKER_SERVER_IP"))
DOCKER_SERVER_USER = config.get("docker", {}).get(
    "server_user", os.getenv("DOCKER_SERVER_USER"))
DOCKER_SERVER_PASSWORD = config.get("docker", {}).get(
    "server_password", os.getenv("DOCKER_SERVER_PASSWORD"))
CONTAINER_NAMES = config.get("docker", {}).get("container_names", [])
RESTART_ORDER = config.get("docker", {}).get("restart_order", [])

if isinstance(CONTAINER_NAMES, str):
    CONTAINER_NAMES = [name.strip() for name in CONTAINER_NAMES.split(',')]
if isinstance(RESTART_ORDER, str):
    RESTART_ORDER = [name.strip() for name in RESTART_ORDER.split(',')]


# Import commands from other files
# Removed 'restart_all_containers' which was causing the ImportError


# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Register Commands ---
# Register your slash commands
# Corresponds to /restart_containers in docker_functions.py
bot.tree.add_command(restart_containers_command)
bot.tree.add_command(restart_plex_command)
bot.tree.add_command(realdebrid_status_command)
bot.tree.add_command(plex_status_command)  # Register the /plex_status command

# --- Bot Events ---


@bot.event
async def on_ready():
    """Event that fires when the bot is ready."""
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    logger.info("Synced commands globally.")

    startup_channel = bot.get_channel(CHANNEL_ID_INT)
    if startup_channel:
        try:
            await startup_channel.send(f"👋 Plex Checker bot is ready! Current logging level: `{configured_log_level_str}`")
            logger.info(
                f"Sent startup message to channel ID: {CHANNEL_ID_INT}")
        except discord.Forbidden:
            logger.error(
                f"Bot does not have permissions to send messages to channel ID: {CHANNEL_ID_INT}")
        except Exception as e:
            logger.error(
                f"Failed to send startup message to channel {CHANNEL_ID_INT}: {e}")
    else:
        logger.warning(
            f"Could not find startup channel with ID: {CHANNEL_ID_INT} or ID was not provided in config.json.")

    if not realdebrid_check_premium_expiry_task.is_running():
        realdebrid_check_premium_expiry_task.start(bot, CHANNEL_ID_INT)
        logger.info("Real-Debrid premium expiry check task started.")

    await setup_media_watcher_service(bot)
    logger.info("Media Watcher Service setup initiated.")


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logger.info("Bot is shutting down.")
    if realdebrid_check_premium_expiry_task.is_running():
        realdebrid_check_premium_expiry_task.cancel()
        logger.info("Real-Debrid premium expiry check task cancelled.")

# --- Main Entry Point ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN environment variable not set. Exiting.")
        exit(1)
    bot.run(DISCORD_TOKEN)
