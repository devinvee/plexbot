import discord
from discord.ext import commands
import os
import logging
import asyncio  # Needed for create_task and managing the loop

# Import functions and commands from your other files
from realdebrid_functions import (
    check_premium_expiry,
    send_realdebrid_startup_status,
    realdebrid_status_command  # The function that handles the command
)
from docker_functions import (
    plex_status_command,       # The function that handles the command
    restart_containers_command,  # The function that handles the command
    restart_plex_command        # The function that handles the command
)

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Variables ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")  # Now loaded here
# Other environment variables are loaded directly in their respective function files

if not DISCORD_TOKEN:
    logging.error("DISCORD_TOKEN environment variable not set. Exiting.")
    exit(1)
if not DISCORD_CHANNEL_ID:
    logging.error("DISCORD_CHANNEL_ID environment variable not set. Exiting.")
    exit(1)

try:
    CHANNEL_ID_INT = int(DISCORD_CHANNEL_ID)
except ValueError:
    logging.error(
        f"DISCORD_CHANNEL_ID '{DISCORD_CHANNEL_ID}' is not a valid integer. Exiting.")
    exit(1)

# Basic checks for other critical environment variables
if not os.environ.get("DOCKER_SERVER_IP") or \
   not os.environ.get("DOCKER_SERVER_USER") or \
   not os.environ.get("DOCKER_SERVER_PASSWORD"):
    logging.warning(
        "Docker server connection details (IP, User, Password) might be missing. Docker commands may fail.")

if not os.environ.get("REALDEBRID_API_KEY"):
    logging.warning(
        "REALDEBRID_API_KEY might be missing. Real-Debrid features may be limited.")


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    """Event that fires when the bot is ready."""
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Bot logged in as {bot.user}")

    # Register commands directly from imported functions
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
    startup_channel = bot.get_channel(CHANNEL_ID_INT)
    if startup_channel:
        await startup_channel.send("👋 Bot is online and ready!")
        # Call the function
        await send_realdebrid_startup_status(startup_channel)
    else:
        logging.warning(
            f"Could not find startup channel with ID: {CHANNEL_ID_INT}")

    # Start the background task, passing the bot object and channel ID
    check_premium_expiry.start(bot, CHANNEL_ID_INT)


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logging.info("Bot is shutting down.")
    check_premium_expiry.cancel()  # Cancel the task

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
