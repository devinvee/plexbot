import os
import logging
import asyncio
import json
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import requests
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount
from utils import load_config
from media_watcher_service import setup_media_watcher_service

try:
    import docker
except ImportError:
    logging.error(
        "The 'docker' library is not installed. Docker commands will not work.")
    docker = None

TEST_GUILD = discord.Object(id=882461504962703381)

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
config = {}
try:
    config = load_config(CONFIG_FILE)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(
        f"Error loading configuration from '{CONFIG_FILE}': {e}. Exiting.")
    exit(1)

new_user_invite_config = config.get("new_user_invite", {})
invite_feature_enabled = new_user_invite_config.get("enabled", False)
invite_role_id = new_user_invite_config.get("role_id")
invite_link_to_send = new_user_invite_config.get("invite_link")


configured_log_level_str = config.get("log_level", "INFO").upper()


LOGGING_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}
log_level_from_dict = LOGGING_LEVELS.get(configured_log_level_str)


log_level = LOGGING_LEVELS.get(configured_log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format='%(asctime)s %(levelname)-8s %(name)-15s %(message)s',
    handlers=[logging.StreamHandler()],
    force=True
)


root_logger = logging.getLogger()
effective_level_numeric = root_logger.getEffectiveLevel()
effective_level_name = logging.getLevelName(effective_level_numeric)
logging.critical(
    f"LOGGING SERVICE: Root logger initialized. Effective log level set to: {effective_level_name} (Numeric: {effective_level_numeric})")
logging.debug(
    "LOGGING SERVICE: This is a test DEBUG message after logging setup.")
logging.info(
    "LOGGING SERVICE: This is a test INFO message after logging setup.")


if log_level <= logging.INFO:
    logging.getLogger('discord').setLevel(logging.INFO)
else:
    logging.getLogger('discord').setLevel(logging.WARNING)

logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

NOTIFICATION_CHANNEL_ID_FROM_CONFIG = config.get(
    "discord", {}).get("notification_channel_id")

if not DISCORD_TOKEN:
    logging.error(
        "DISCORD_TOKEN environment variable not set. Please add it to your .env file. Exiting.")
    exit(1)

CHANNEL_ID_INT = None
if NOTIFICATION_CHANNEL_ID_FROM_CONFIG:
    try:
        CHANNEL_ID_INT = int(NOTIFICATION_CHANNEL_ID_FROM_CONFIG)
    except ValueError:
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
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)


def _get_docker_client():
    if not docker:
        return None
    try:
        return docker.from_env()
    except Exception as e:
        logging.error(
            f"Could not connect to Docker daemon: {e}. Is Docker running and socket mounted correctly?")
        return None


class LibrarySelectView(discord.ui.View):
    def __init__(self, libraries):
        super().__init__(timeout=300)

        select_options = [
            discord.SelectOption(label=lib.title) for lib in libraries
        ]

        self.add_item(discord.ui.Select(
            placeholder="Choose the libraries you're interested in...",
            options=select_options,
            min_values=1,
            max_values=len(libraries),
            custom_id="library_select"
        ))

    @discord.ui.select(custom_id="library_select")
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_libraries = select.values

        await interaction.response.send_message(f"Thanks! I've noted your interest in: **{', '.join(selected_libraries)}**.", ephemeral=True)

# --- Discord Bot Commands ---


@bot.command()
@commands.is_owner()
async def sync(ctx: commands.Context):
    """Manually syncs slash commands to the current guild."""
    try:
        await ctx.send(f"Attempting to sync commands to this server...")
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"✅ Successfully synced **{len(synced)}** commands.")
    except Exception as e:
        await ctx.send(f"❌ Failed to sync commands: {e}")


@bot.tree.command(name="plexstatus", description="Check Plex Docker container status.")
async def plex_status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    try:
        plex_container = client.containers.get("plex")
        status = plex_container.status
        await interaction.followup.send(f"🎬 Plex container status: `{status}`")
    except docker.errors.NotFound:
        await interaction.followup.send("Plex container not found. Check your container name.", ephemeral=True)
    except Exception as e:
        logging.error(f"Error checking Plex status: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)


@bot.tree.command(name="restartcontainers", description="Restart specified Docker containers in order.")
async def restart_containers_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)  # Defer publicly

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    containers_to_restart_in_order = [
        "qbittorrent",
        "sonarr",
        "radarr",
        "plex"
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
            await asyncio.sleep(5)
        except docker.errors.NotFound:
            await interaction.followup.send(f"⚠️ Container `{name}` not found. Skipping.", ephemeral=False)
        except Exception as e:
            logging.error(f"Error restarting container {name}: {e}")
            await interaction.followup.send(f"❌ Error restarting `{name}`: {e}", ephemeral=False)

    await interaction.followup.send("✨ All specified containers processed.")


# ---- Currently broken plexaccess command allowing users to select which libraries they want to receive access to ---
# @bot.tree.command(name="plexaccess", description="Select which Plex libraries you are interested in.")
# async def plexaccess_command(interaction: discord.Interaction):
#     await interaction.response.defer(ephemeral=False)
#     try:

#         plex_url = os.getenv("PLEX_URL")
#         plex_token = os.getenv("PLEX_TOKEN")

#         if not plex_url or not plex_token:
#             await interaction.followup.send("Plex server is not configured. Please contact the admin.")
#             return

#         account = MyPlexAccount(token=plex_token)
#         plex = PlexServer(plex_url, account.authenticationToken)
#         libraries = plex.library.sections()

#         view = LibrarySelectView(libraries)

#         await interaction.followup.send(
#             "Awesome! So you'd like Plex access? Which libraries are you interested in?",
#             view=view
#         )

#     except Exception as e:
#         logging.error(f"Failed to execute /plexaccess command: {e}")
#         await interaction.followup.send(f"An error occurred while fetching Plex libraries: {e}")


@bot.tree.command(name="restartplex", description="Restart Plex container.")
async def restart_plex_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    client = _get_docker_client()
    if not client:
        await interaction.followup.send("Cannot connect to Docker daemon. Docker commands are unavailable.", ephemeral=True)
        return

    try:
        plex_container = client.containers.get("plex")

        await interaction.followup.send("🔄 Restarting Plex container...", ephemeral=False)
        plex_container.restart(timeout=30)
        await asyncio.sleep(5)
        plex_container.reload()
        status = plex_container.status
        await interaction.followup.send(f"✅ Plex container restart initiated. Current status: `{status}`")

    except docker.errors.NotFound:
        await interaction.followup.send("Plex container not found. Check your container name.", ephemeral=True)
    except Exception as e:
        logging.error(f"Error restarting Plex: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

# --- Message handler for /realdebrid ---


@bot.tree.command(name="realdebrid", description="Check your Real-Debrid account status.")
async def realdebrid_status_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

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

        premium_status = "Premium" if user_info.get(
            "type") == "premium" else "Free"
        email = user_info.get("email")

        premium_expires_str = user_info.get("expiration")
        if premium_expires_str:
            if premium_expires_str.endswith('Z'):
                premium_expires_str = premium_expires_str[:-1] + '+00:00'

            premium_expires_dt = datetime.fromisoformat(premium_expires_str)
            logging.debug(
                f"premium expiration date return value: {premium_expires_dt}")

            now_utc = datetime.now(timezone.utc)
            logging.debug(f"Current date and time in UTC: {now_utc}")
            days_left = (premium_expires_dt - now_utc).days
            logging.debug(f"Days left: {days_left}")

            formatted_expiry_date = premium_expires_dt.strftime("%B %d, %Y")
            logging.debug(f"Formatted expiry date: {formatted_expiry_date}")

            if days_left >= 0:
                expiry_message = f"Expires in `{days_left}` days (on `{formatted_expiry_date}`)."
                logging.info(f"Your account expires in {days_left} days.")
            else:
                expiry_message = f"Expired `{-days_left}` days ago (on `{formatted_expiry_date}`)."
                logging.warning(f"Your account is expired!")
        else:
            expiry_message = "No expiry date found."
            logging.debug(
                f"expiry string value: {premium_expires_str}. No expiry date found here.")

        message = (
            f"**Real-Debrid Account Status:**\n"
            f"📧 Email: `{email}`\n"
            f"✨ Status: `{premium_status}`\n"
            f"🗓️ {expiry_message}\n"
        )
        await interaction.followup.send(message, ephemeral=False)

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Real-Debrid status: {e}")
        await interaction.followup.send(f"An error occurred fetching Real-Debrid status: {e}", ephemeral=True)
    except Exception as e:
        logging.error(f"An unexpected error occurred with Real-Debrid: {e}")
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)

# --- Background Task for Real-Debrid Premium Expiry ---


@tasks.loop(hours=24)
async def check_premium_expiry():
    rd_api_key = os.getenv("REALDEBRID_API_KEY")
    if not rd_api_key:
        logging.warning(
            "Real-Debrid API key not available for premium expiry check. Skipping task.")
        check_premium_expiry.cancel()
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

            if days_left <= 7:
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

    try:
        synced = await bot.tree.sync(guild=TEST_GUILD)

        logging.info(
            f"Synced {len(synced)} command(s) to guild {TEST_GUILD.id}.")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

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

    if not check_premium_expiry.is_running():
        check_premium_expiry.start()
        logging.info("Real-Debrid premium expiry check task started.")

    await setup_media_watcher_service(bot)
    logging.info("Media Watcher Service setup initiated.")


# --- Event handler for role updates ---


@bot.event
async def on_member_update(before, after):
    """
    Event that fires when a user's server profile is updated,
    including when they are assigned a new role.
    """
    logging.info(
        f"on_member_update event triggered for member: {after.display_name} (ID: {after.id})")

    if not invite_feature_enabled:
        logging.debug(
            "Invite feature is disabled in config. Exiting function.")
        return
    if not invite_role_id or not invite_link_to_send:
        logging.warning(
            "New user invite feature is enabled, but role_id or invite_link is missing from config.")
        return

    logging.debug(
        f"Roles before for {after.display_name}: {[role.name for role in before.roles]}")
    logging.debug(
        f"Roles after for {after.display_name}: {[role.name for role in after.roles]}")

    if before.roles == after.roles:
        logging.debug(
            f"No role change detected for {after.display_name}. Exiting function.")
        return

    try:
        target_role_id = int(invite_role_id)
        target_role = after.guild.get_role(target_role_id)
    except (ValueError, TypeError):
        logging.error(
            f"Invalid 'role_id' for new user invite feature: {invite_role_id}. It must be a valid integer.")
        return

    if not target_role:
        logging.warning(
            f"Could not find the role with ID {invite_role_id} in the server '{after.guild.name}'.")
        return

    was_added = target_role not in before.roles and target_role in after.roles

    logging.info(
        f"Checking if role '{target_role.name}' was added to {after.display_name}... Result: {was_added}")

    if was_added:
        logging.info(
            f"User '{after.display_name}' was assigned the role '{target_role.name}'. Preparing to send invite DM.")

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


@bot.event
async def on_close():
    """Event that fires when the bot is closing."""
    logging.info("Bot is shutting down.")
    if check_premium_expiry.is_running():
        check_premium_expiry.cancel()  # Cancel the Real-Debrid task
        logging.info("Real-Debrid premium expiry check task cancelled.")

# --- Main Entry Point ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
