import os
import logging
import asyncio
import json
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import requests

# Import the shared utility function to load configuration
from utils import load_config

# Import the media watcher service setup function
from media_watcher_service import setup_media_watcher_service

# Import docker library for Docker interaction (ensure 'docker' is in requirements.txt)
try:
    import docker
except ImportError:
    # This initial error logging will use Python's default logging behavior
    # which is usually INFO or WARNING level to console.
    logging.error(
        "The 'docker' library is not installed. Docker commands will not work.")
    docker = None  # Set to None so we can check if it's available

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
config = {}
try:
    config = load_config(CONFIG_FILE)
    # This print confirmed "DEBUG" is loaded into the config object
except (FileNotFoundError, json.JSONDecodeError) as e:
    # This log will use Python's default (or any pre-existing) logger settings.
    logging.error(
        f"Error loading configuration from '{CONFIG_FILE}': {e}. Exiting.")
    exit(1)

# --- NEW: Load New User Invite Configuration ---
new_user_invite_config = config.get("new_user_invite", {})
invite_feature_enabled = new_user_invite_config.get("enabled", False)
invite_role_id = new_user_invite_config.get("role_id")
invite_link_to_send = new_user_invite_config.get("invite_link")
# --- END NEW ---

# --- Logging Setup (MOVED AND MODIFIED) ---
# 1. Get the desired log level from config.json, defaulting to INFO if not found
configured_log_level_str = config.get("log_level", "INFO").upper()


# 2. Map the string level to a logging constant
LOGGING_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}
log_level_from_dict = LOGGING_LEVELS.get(configured_log_level_str)


# 3. Get the actual logging level constant. Use INFO as a fallback for invalid strings.
log_level = LOGGING_LEVELS.get(configured_log_level_str, logging.INFO)

# 4. Re-configure the basic logger with the dynamic level
# We re-run basicConfig here, which effectively updates the root logger's level.
# It's important to do this *after* config is loaded.
# Attempt to force configuration (Python 3.8+)
# If you are on Python < 3.8, force=True will cause an error. Remove it if so.
logging.basicConfig(
    level=log_level,  # Use the configured log level here #
    format='%(asctime)s %(levelname)-8s %(name)-15s %(message)s',
    # Ensure it still outputs to stdout for Docker logs
    handlers=[logging.StreamHandler()],
    force=True  # Add this to ensure reconfiguration
)


# CRITICAL Diagnostic Log
root_logger = logging.getLogger()
effective_level_numeric = root_logger.getEffectiveLevel()
effective_level_name = logging.getLevelName(effective_level_numeric)
logging.critical(
    f"LOGGING SERVICE: Root logger initialized. Effective log level set to: {effective_level_name} (Numeric: {effective_level_numeric})")
logging.debug(
    "LOGGING SERVICE: This is a test DEBUG message after logging setup.")
logging.info(
    "LOGGING SERVICE: This is a test INFO message after logging setup.")


# 5. Set specific log levels for chatty libraries.
# You can make discord.py logs INFO or DEBUG if your main log_level is DEBUG,
# otherwise keep them at WARNING to avoid excessive output.
if log_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
else:
    logging.getLogger('discord').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
# For Flask server logs, often very verbose
logging.getLogger('werkzeug').setLevel(logging.WARNING)
# --- END LOGGING SETUP ---


# Retrieve Discord token directly from environment variables (best practice)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Get Discord channel ID from the loaded config
NOTIFICATION_CHANNEL_ID_FROM_CONFIG = config.get(
    "discord", {}).get("notification_channel_id")

if not DISCORD_TOKEN:
    # This log will now use the new, configured log level
    logging.error(
        "DISCORD_TOKEN environment variable not set. Please add it to your .env file. Exiting.")
    exit(1)

# Convert channel ID to integer, handle errors
CHANNEL_ID_INT = None
if NOTIFICATION_CHANNEL_ID_FROM_CONFIG:
    try:
        CHANNEL_ID_INT = int(NOTIFICATION_CHANNEL_ID_FROM_CONFIG)
    except ValueError:
        # This log will also use the new, configured log level
        logging.error(
            f"Discord notification_channel_id '{NOTIFICATION_CHANNEL_ID_FROM_CONFIG}' in config.json is not a valid integer. Check your .env value for DISCORD_CHANNEL_ID.")
else:
    logging.warning(
        "Discord notification_channel_id not set in config.json. Some bot functions may not work.")

if log_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
else:
    logging.getLogger('discord').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# --- Discord Bot Setup ---
intents = discord.Intents.default()
# Required for reading message content if you have text commands
intents.message_content = True
intents.members = True  # <-- IMPORTANT: Enable the members intent
bot = commands.Bot(command_prefix="/", intents=intents)

# --- Docker Interaction Helper (Optional, but useful for avoiding repetitive code) ---
# Ensure your docker-compose.yml has `- /var/run/docker.sock:/var/run/docker.sock:ro` for this to work


def _get_docker_client():
    if not docker:
        return None
    try:
        return docker.from_env()
    except Exception as e:
        logging.error(
            f"Could not connect to Docker daemon: {e}. Is Docker running and socket mounted correctly?")
        return None

# --- Discord Bot Commands ---


# 1. Plex Status Command
@bot.tree.command(name="plexstatus", description="Check Plex Docker container status.")
async def plex_status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    try:
        # <<--- CHANGE "Plex" to your actual Plex container name
        plex_container = client.containers.get("Plex")
        status = plex_container.status
        await interaction.followup.send(f"🎬 Plex container status: `{status}`")
    except docker.errors.NotFound:
        await interaction.followup.send("Plex container not found. Check your container name.", ephemeral=True)
    except Exception as e:
        logging.error(f"Error checking Plex status: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

# 2. Restart Containers Command


@bot.tree.command(name="restartcontainers", description="Restart specified Docker containers in order.")
async def restart_containers_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    # <<--- IMPORTANT: Customize this list with your actual container names and desired restart order
    containers_to_restart_in_order = [
        "qbittorrent",  # Example: Restart download client first
        "sonarr",       # Example: Restart Sonarr next
        "radarr",       # Example: Restart Radarr next
        # Example: Restart Plex last (after its dependencies are up)
        "Plex"
    ]

    await interaction.followup.send("🔄 Attempting to restart specified containers in order...", ephemeral=False)

    for name in containers_to_restart_in_order:
        try:
            container = client.containers.get(name)
            await interaction.followup.send(f"Stopping `{name}`...", ephemeral=False)
            container.stop(timeout=30)  # Add timeout for graceful stop
            await interaction.followup.send(f"Starting `{name}`...", ephemeral=False)
            container.start()
            await interaction.followup.send(f"✅ `{name}` restarted successfully.")
            await asyncio.sleep(5)  # Small delay between container restarts
        except docker.errors.NotFound:
            await interaction.followup.send(f"⚠️ Container `{name}` not found. Skipping.", ephemeral=False)
        except Exception as e:
            logging.error(f"Error restarting container {name}: {e}")
            await interaction.followup.send(f"❌ Error restarting `{name}`: {e}", ephemeral=False)

    await interaction.followup.send("✨ All specified containers processed.")


# 3. Restart Plex Command
@bot.tree.command(name="restartplex", description="Restart Plex container.")
async def restart_plex_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    try:
        # <<--- CHANGE "Plex" to your actual Plex container name
        plex_container = client.containers.get("Plex")

        await interaction.followup.send("🔄 Restarting Plex container...", ephemeral=False)
        plex_container.restart(timeout=30)  # Add timeout for graceful restart
        # Give Docker a moment to update status
        await asyncio.sleep(5)
        plex_container.reload()  # Reload container info to get updated status
        status = plex_container.status
        await interaction.followup.send(f"✅ Plex container restart initiated. Current status: `{status}`")

    except docker.errors.NotFound:
        await interaction.followup.send("Plex container not found. Check your container name.", ephemeral=True)
    except Exception as e:
        logging.error(f"Error restarting Plex: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)


# 4. Real-Debrid Status Command
@bot.tree.command(name="realdebrid", description="Check your Real-Debrid account status.")
async def realdebrid_status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        await interaction.followup.send("Real-Debrid API key not configured in .env.", ephemeral=True)
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {"Authorization": f"Bearer {rd_api_key}"}

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers)
        response.raise_for_status()
        user_info = response.json()

        premium_status = "Premium" if user_info.get("premium") == 1 else "Free"
        email = user_info.get("email")

        premium_expires_str = user_info.get("premium_expire")  # "YYYY-MM-DD"
        if premium_expires_str:
            premium_expires = datetime.strptime(
                premium_expires_str, "%Y-%m-%d")
            days_left = (premium_expires - datetime.now()).days
            expiry_message = f"Expires in `{days_left}` days (`{premium_expires_str}`)."
        else:
            expiry_message = "No expiry date found."

        message = (
            f"**Real-Debrid Account Status:**\n"
            f"📧 Email: `{email}`\n"
            f"✨ Status: `{premium_status}`\n"
            f"🗓️ {expiry_message}"
        )
        await interaction.followup.send(message, ephemeral=False)

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Real-Debrid status: {e}")
        await interaction.followup.send(f"An error occurred fetching Real-Debrid status: {e}", ephemeral=True)
    except Exception as e:
        logging.error(f"An unexpected error occurred with Real-Debrid: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

# --- Background Task for Real-Debrid Premium Expiry ---


@tasks.loop(hours=24)  # Checks once every 24 hours
async def check_premium_expiry():
    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        logging.warning(
            "Real-Debrid API key not available for premium expiry check. Skipping task.")
        check_premium_expiry.cancel()  # Cancel the task if no key
        return

    if CHANNEL_ID_INT is None:
        logging.warning(
            "Discord notification channel ID not set for Real-Debrid expiry. Skipping task.")
        check_premium_expiry.cancel()
        return

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {"Authorization": f"Bearer {rd_api_key}"}

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers)
        response.raise_for_status()
        user_info = response.json()

        premium_expires_str = user_info.get("premium_expire")
        if premium_expires_str:
            premium_expires = datetime.strptime(
                premium_expires_str, "%Y-%m-%d")
            days_left = (premium_expires - datetime.now()).days

            if days_left <= 7:  # Notify if 7 days or less
                channel = bot.get_channel(CHANNEL_ID_INT)
                if channel:
                    message = f"🚨 **Real-Debrid Premium Warning!** 🚨\nYour Real-Debrid premium expires in **{days_left} days** (`{premium_expires_str}`). Renew soon!"
                    await channel.send(message)
                    logging.info(
                        f"Real-Debrid expiry notification sent: {days_left} days left.")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error checking Real-Debrid premium expiry: {e}")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during Real-Debrid premium expiry check: {e}")

# --- Discord Bot Events ---


@bot.event
async def on_ready():
    """Event that fires when the bot is ready and connected to Discord."""
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Consider removing print if logs are sufficient
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Sync slash commands globally (or to specific guild for faster testing)
    try:
        synced = await bot.tree.sync()  # Syncs globally
        # If testing rapidly, you might use:
        # TEST_GUILD_ID = 123456789012345678 # Replace with your test guild ID
        # guild = discord.Object(id=TEST_GUILD_ID)
        # synced = await bot.tree.sync(guild=guild)

        logging.info(f"Synced {len(synced)} command(s) globally.")
        # Consider removing print if logs are sufficient
        print(f"Synced {len(synced)} command(s) globally.")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")
        # Consider removing print if logs are sufficient
        print(f"Failed to sync commands: {e}")

    # Send startup message to Discord channel
    startup_channel = None
    if CHANNEL_ID_INT:
        startup_channel = bot.get_channel(CHANNEL_ID_INT)

    if startup_channel:
        1 == 1
        # await startup_channel.send("👋 Bot is online and ready!")
        # You might add a call here to send initial Real-Debrid status if desired
        # await send_realdebrid_startup_status(startup_channel) # (Requires defining send_realdebrid_startup_status)
    else:
        logging.warning(
            f"Could not find startup channel with ID: {CHANNEL_ID_INT} or ID was not provided in config.json.")

    # Start the background tasks
    if not check_premium_expiry.is_running():
        check_premium_expiry.start()
        logging.info("Real-Debrid premium expiry check task started.")

    # --- NEW: Setup Media Watcher Service ---
    # This will start the Flask webhook server and the Overseerr user sync task
    # Pass the bot instance to the service
    await setup_media_watcher_service(bot)
    logging.info("Media Watcher Service setup initiated.")
    # --- END NEW ---

# --- NEW: Event handler for role updates ---


@bot.event
async def on_member_update(before, after):
    """
    Event that fires when a user's server profile is updated,
    including when they are assigned a new role.
    """
    # 1. Check if the feature is enabled and properly configured
    if not invite_feature_enabled:
        return
    if not invite_role_id or not invite_link_to_send:
        logging.warning(
            "New user invite feature is enabled, but role_id or invite_link is missing from config.")
        return

    # 2. Check if roles have actually changed to avoid unnecessary processing
    if before.roles == after.roles:
        return

    # 3. Get the specific role object from the server
    try:
        # The role ID from config might be a string, convert to int
        target_role_id = int(invite_role_id)
        target_role = after.guild.get_role(target_role_id)
    except (ValueError, TypeError):
        logging.error(
            f"Invalid 'role_id' for new user invite feature: {invite_role_id}. It must be a valid integer.")
        return  # Stop if the role_id is invalid

    if not target_role:
        logging.warning(
            f"Could not find the role with ID {invite_role_id} in the server '{after.guild.name}'.")
        return  # Stop if the role doesn't exist

    # 4. Check if the target role was ADDED in this update
    was_added = target_role not in before.roles and target_role in after.roles

    if was_added:
        logging.info(
            f"User '{after.display_name}' was assigned the role '{target_role.name}'. Preparing to send invite DM.")

        # 5. Construct and send the direct message
        message = (
            f"Hello {after.display_name}!\n\n"
            f"Welcome! As you've been assigned the '{target_role.name}' role, here is your special invite link:\n"
            f"{invite_link_to_send}"
        )

        try:
            await after.send(message)
            logging.info(
                f"Successfully sent invite DM to '{after.display_name}'.")
        except discord.Forbidden:
            logging.warning(
                f"Could not send DM to '{after.display_name}'. They may have DMs disabled.")
        except Exception as e:
            logging.error(
                f"An unexpected error occurred while sending DM to '{after.display_name}': {e}", exc_info=True)
# --- END NEW ---


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logging.info("Bot is shutting down.")
    if check_premium_expiry.is_running():
        check_premium_expiry.cancel()  # Cancel the Real-Debrid task
        logging.info("Real-Debrid premium expiry check task cancelled.")
    # The Flask server thread (if daemon) will exit with the main program.

# --- Main Entry Point ---
if __name__ == "__main__":
    # The `load_dotenv()` call is handled internally by `utils.py` when imported.
    # This bot.run call is the blocking operation that keeps the bot alive.
    bot.run(DISCORD_TOKEN)
