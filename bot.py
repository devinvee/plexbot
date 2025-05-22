import os
import logging
import asyncio
import discord
from discord.ext import commands, tasks
# Import the shared utility function
from utils import load_config
from media_watcher_service import setup_media_watcher_service

# --- Configuration Loading ---
# This will load variables from .env into os.environ AND parse config.json
# It's crucial for `load_dotenv()` to be called early,
# but it's now handled by the import of `utils` which calls it internally.
CONFIG_FILE = "config.json"
try:
    config = load_config(CONFIG_FILE)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading configuration: {e}")
    exit(1)

# Retrieve Discord token directly from environment variables (best practice)
# This was already being done, keep it.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Get Discord channel ID from the loaded config
NOTIFICATION_CHANNEL_ID_FROM_CONFIG = config.get(
    "discord", {}).get("notification_channel_id")

if not DISCORD_TOKEN:
    logging.error("DISCORD_TOKEN environment variable not set. Exiting.")
    exit(1)
if not NOTIFICATION_CHANNEL_ID_FROM_CONFIG:
    logging.warning(
        "Discord notification_channel_id not set in config.json. Real-Debrid startup status will go nowhere or DMs only.")

try:
    CHANNEL_ID_INT = int(
        NOTIFICATION_CHANNEL_ID_FROM_CONFIG) if NOTIFICATION_CHANNEL_ID_FROM_CONFIG else None
except ValueError:
    logging.error(
        f"Discord notification_channel_id '{NOTIFICATION_CHANNEL_ID_FROM_CONFIG}' in config.json is not a valid integer. Exiting.")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# --- Your existing commands (ensure they take 'config' if needed) ---

# Example: If your realdebrid functions need config, update their definitions
# Make sure these functions (realdebrid_status_command, send_realdebrid_startup_status, check_premium_expiry)
# are defined in your bot.py or imported and correctly accept 'config'.
# I'll just put placeholders here, assuming your original code had them defined.

# Placeholder for realdebrid_status_command - adjust if it needs config


async def realdebrid_status_command(interaction: discord.Interaction):
    # Still from env for RealDebrid
    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        await interaction.response.send_message("Real-Debrid API key not configured.", ephemeral=True)
        return
    # ... rest of the command logic ...

# Placeholder for send_realdebrid_startup_status


# Needs config_data
async def send_realdebrid_startup_status(channel, config_data):
    # Still from env for RealDebrid
    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        logging.warning(
            "Real-Debrid API key not available for startup status.")
        return
    # ... rest of the function logic using rd_api_key ...

# Placeholder for check_premium_expiry


@tasks.loop(hours=24)
# Needs config_data
async def check_premium_expiry(bot_instance, channel_id, config_data):
    # Still from env for RealDebrid
    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        logging.warning(
            "Real-Debrid API key not available for premium expiry check.")
        return
    # ... rest of the task logic using rd_api_key ...


@bot.event
async def on_ready():
    """Event that fires when the bot is ready."""
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Bot logged in as {bot.user}")

    # Register commands (ensure functions are defined/imported)
    bot.tree.command(name="plexstatus", description="Check Plex Docker container status.")(
        plex_status_command)
    bot.tree.command(name="restartcontainers", description="Restart specified Docker containers in order.")(
        restart_containers_command)
    bot.tree.command(name="restartplex", description="Restart Plex container.")(
        restart_plex_command)
    bot.tree.command(
        name="realdebrid", description="Check your Real-Debrid account status.")(realdebrid_status_command)

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s) globally.")
        print(f"Synced {len(synced)} command(s) globally.")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")
        print(f"Failed to sync commands: {e}")

    # Send startup message and Real-Debrid status
    startup_channel = bot.get_channel(
        CHANNEL_ID_INT) if CHANNEL_ID_INT else None
    if startup_channel:
        await startup_channel.send("👋 Bot is online and ready!")
        # Pass config to Real-Debrid status function if it needs it (e.g., for API key)
        await send_realdebrid_startup_status(startup_channel, config)
    else:
        logging.warning(
            f"Could not find startup channel with ID: {CHANNEL_ID_INT} or ID was not provided in config.json.")

    # Start the background tasks
    if CHANNEL_ID_INT:  # Only start if a valid channel ID is available
        check_premium_expiry.start(bot, CHANNEL_ID_INT, config)  # Pass config
    else:
        logging.warning(
            "Skipping Real-Debrid premium expiry check as no valid channel ID is set.")

    # --- NEW: Setup Media Watcher Service ---
    # This will start the Flask webhook server and the Overseerr user sync task
    await setup_media_watcher_service(bot)  # Pass the bot instance
    # --- END NEW ---


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logging.info("Bot is shutting down.")
    check_premium_expiry.cancel()  # Cancel the Real-Debrid task
    # Flask thread (if daemon) will exit with the main program

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
