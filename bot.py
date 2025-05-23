from realdebrid_functions import realdebrid_status_command
from realdebrid_functions import check_premium_expiry as realdebrid_check_premium_expiry_task
from docker_functions import (
    restart_all_containers, restart_containers_logic, restart_plex_command,
    _get_docker_client  # Import the helper to be passed around
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
from utils import load_config, load_dotenv  # Import load_dotenv from utils

# --- Initial Environment Variable Loading ---
# Load environment variables from .env file immediately.
# This MUST happen before trying to read LOG_LEVEL from os.getenv().
load_dotenv()

# --- Logging Setup (EARLY AND PRIMARY CONFIGURATION) ---
# 1. Get the desired log level from the LOG_LEVEL environment variable directly,
#    defaulting to 'INFO' if not found.
configured_log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()

# 2. Map the string level to a logging constant
LOGGING_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

# 3. Get the actual logging level constant. Use INFO as a fallback for invalid strings.
numeric_level = LOGGING_LEVELS.get(configured_log_level_str, logging.INFO)

# 4. Apply basic logging configuration
#    This will affect all loggers that don't have their own specific level set.
logging.basicConfig(
    level=numeric_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Logs to console (stderr by default)
    ]
)

# 5. Set discord.py's internal logging level to match if desired
#    This is important for seeing discord.client and discord.gateway messages at your desired level.
#    Only set discord.py logs to INFO if the overall level is INFO or DEBUG, otherwise keep at WARNING.
if numeric_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
    logging.getLogger('discord.http').setLevel(
        logging.INFO)  # For detailed HTTP requests/responses
else:
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(
    logging.WARNING)  # Reduce asyncio verbosity
logging.getLogger('requests').setLevel(
    logging.WARNING)  # Reduce requests verbosity
# For Flask server logs, often very verbose
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# Now that logging is configured, we can safely get a logger for this module
logger = logging.getLogger(__name__)
logger.info(f"Main bot logging level set to: {configured_log_level_str}")


# Import the media watcher service setup function

# Import docker library for Docker interaction (ensure 'docker' is in requirements.txt)
try:
    import docker
except ImportError:
    logger.error(
        "The 'docker' library is not installed. Docker commands will not work.", exc_info=True)
    docker = None  # Set to None so we can check if it's available


# --- Configuration Loading ---
CONFIG_FILE = "config.json"
config = {}
try:
    # utils.load_config will now use the already loaded environment variables via load_dotenv()
    config = load_config(CONFIG_FILE)
    logger.info("Configuration loaded successfully.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.critical(
        f"Error loading configuration from '{CONFIG_FILE}': {e}. Exiting.", exc_info=True)
    exit(1)  # Exit if essential config cannot be loaded

# --- Extract configurations from the loaded config ---
# Use .get() with a default value to prevent KeyError if the key is missing
DISCORD_TOKEN = config.get("discord_token", os.getenv(
    "DISCORD_TOKEN"))  # Fallback to ENV if not in config
REALDEBRID_API_KEY = config.get("realdebrid_api_key", os.getenv(
    "REALDEBRID_API_KEY"))  # Fallback to ENV
CHANNEL_ID = config["discord"].get("notification_channel_id")
CHANNEL_ID_INT = int(
    CHANNEL_ID) if CHANNEL_ID and CHANNEL_ID.isdigit() else None

# Docker details from config or environment
DOCKER_SERVER_IP = config.get("docker", {}).get(
    "server_ip", os.getenv("DOCKER_SERVER_IP"))
DOCKER_SERVER_USER = config.get("docker", {}).get(
    "server_user", os.getenv("DOCKER_SERVER_USER"))
DOCKER_SERVER_PASSWORD = config.get("docker", {}).get(
    "server_password", os.getenv("DOCKER_SERVER_PASSWORD"))
CONTAINER_NAMES = config.get("docker", {}).get("container_names", [])
RESTART_ORDER = config.get("docker", {}).get("restart_order", [])

# Ensure container names and restart order are lists
if isinstance(CONTAINER_NAMES, str):
    CONTAINER_NAMES = [name.strip() for name in CONTAINER_NAMES.split(',')]
if isinstance(RESTART_ORDER, str):
    RESTART_ORDER = [name.strip() for name in RESTART_ORDER.split(',')]


# Import commands from other files AFTER environment variables are loaded by load_dotenv()
# This ensures they pick up DOCKER_SERVER_IP etc. if they read from os.environ directly.

# --- Discord Bot Setup ---
intents = discord.Intents.default()
# Required for message content (e.g., if prefix commands were used)
intents.message_content = True
# Ensure this is enabled if you need to fetch member data, e.g., for DMs
intents.members = True
# Changed prefix from '/' to '!' to avoid conflict with slash commands
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Register Commands ---
# Register your slash commands
bot.tree.add_command(restart_all_containers)
bot.tree.add_command(restart_plex_command)
bot.tree.add_command(realdebrid_status_command)

# --- Bot Events ---


@bot.event
async def on_ready():
    """Event that fires when the bot is ready."""
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()  # Sync slash commands globally
    logger.info("Synced commands globally.")

    # Get the startup channel if configured
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

    # Start the Real-Debrid background task
    if not realdebrid_check_premium_expiry_task.is_running():
        # Pass the bot instance and the channel ID to the task
        realdebrid_check_premium_expiry_task.start(bot, CHANNEL_ID_INT)
        logger.info("Real-Debrid premium expiry check task started.")

    # --- NEW: Setup Media Watcher Service ---
    # Pass the bot instance and the shared config for the service
    await setup_media_watcher_service(bot)
    logger.info("Media Watcher Service setup initiated.")
    # --- END NEW ---


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logger.info("Bot is shutting down.")
    if realdebrid_check_premium_expiry_task.is_running():
        realdebrid_check_premium_expiry_task.cancel()  # Cancel the Real-Debrid task
        logger.info("Real-Debrid premium expiry check task cancelled.")
    # The Flask server thread (if daemon) will exit with the main program.

# --- Main Entry Point ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN environment variable not set. Exiting.")
        exit(1)
    bot.run(DISCORD_TOKEN)
