import os
import json
import logging
from flask import Flask, request, jsonify
import asyncio
import requests
import re
from collections import deque
import discord  # Import discord.py for type hinting or actual use if bot instance is passed

# Import the shared utility function (assuming utils.py is in the same directory)
from utils import load_config

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
config = {}  # Initialize config
try:
    # This will use the environment variables already loaded by bot.py via utils.load_dotenv()
    config = load_config(CONFIG_FILE)
    # This log will now respect the logging configuration set up in bot.py
    # because the logger is obtained *after* basicConfig has run in bot.py.
    # We delay getting the logger until after this initial config load for clarity.
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading configuration in media_watcher_service: {e}")
    exit(1)  # Exit if essential config cannot be loaded

# --- Logging Setup for this module ---
# Just get a named logger. The overall level will be set by bot.py's basicConfig.
# Remove the redundant basicConfig call here, as bot.py already handles the root logger.
logger = logging.getLogger(__name__)
# This will now respect the main log level
logger.info("Media Watcher Service module initialized.")

# Ensure required config sections exist
DISCORD_CONFIG = config.get("discord", {})
OVERSEERR_CONFIG = config.get("overseerr", {})
SONARR_INSTANCES = config.get("sonarr_instances", [])
USER_MAPPINGS = config.get("user_mappings", {}).get("plex_to_discord", {})

NOTIFICATION_CHANNEL_ID = DISCORD_CONFIG.get("notification_channel_id")
DM_NOTIFICATIONS_ENABLED = DISCORD_CONFIG.get("dm_notifications_enabled", True)

if not NOTIFICATION_CHANNEL_ID:
    logger.warning(
        "Discord notification_channel_id not set in config.json. Only DMs (if enabled) will work.")

# --- Global State for User Data and De-duplication ---
OVERSEERR_USERS_DATA = {}
NOTIFIED_EPISODES_CACHE = deque(maxlen=1000)

app = Flask(__name__)

# --- Helper Functions ---


def normalize_plex_username(username: str) -> str:
    """Converts a plex username to a consistent format for tag matching."""
    return username.lower().replace(" ", "")


async def fetch_overseerr_users():
    """Fetches users from Overseerr API and populates OVERSEERR_USERS_DATA."""
    if not OVERSEERR_CONFIG.get("base_url") or not OVERSEERR_CONFIG.get("api_key"):
        logger.warning("Overseerr API config missing. Skipping user sync.")
        return

    url = f"{OVERSEERR_CONFIG['base_url'].rstrip('/')}/api/v1/user"
    logger.info(f"Attempting to fetch Overseerr users from: {url}")

    headers = {
        "X-Api-Key": OVERSEERR_CONFIG['api_key'], "Accept": "application/json"}

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)

        logger.info(
            f"Overseerr API Response Status Code: {response.status_code}")
        logger.debug(f"Overseerr API Response Headers: {response.headers}")
        logger.debug(
            f"Overseerr API Raw Response Text (first 500 chars): {response.text[:500]}...")

        response.raise_for_status()

        parsed_data = None

        try:
            parsed_data = response.json()
            logger.info("Successfully parsed Overseerr API response as JSON.")
            logger.debug(f"Type of parsed data object: {type(parsed_data)}")
            logger.debug(f"Content of parsed data (pageInfo & first 2 results): "
                         f"pageInfo={parsed_data.get('pageInfo')}, "
                         f"results (first 2)={parsed_data.get('results', [])[:2]}")

        except requests.exceptions.JSONDecodeError as e:
            logger.error(
                f"Failed to decode JSON response from Overseerr: {e}")
            logger.error(f"Full problematic response text: {response.text}")
            return

        users_list = parsed_data.get('results')

        if not isinstance(users_list, list):
            logger.error(
                f"Overseerr API 'results' key did not contain a list. Got: {type(users_list)}. Cannot process users.")
            return

        OVERSEERR_USERS_DATA.clear()
        for user in users_list:
            if not isinstance(user, dict):
                logger.warning(
                    f"Skipping unexpected item in Overseerr users list. Expected dict, got {type(user)}: {user}")
                continue

            plex_username = user.get('plexUsername')

            if plex_username is None:
                logger.warning(
                    f"User {user.get('displayName', user.get('email', user.get('id', 'Unknown')))} has no 'plexUsername'. Skipping for mapping.")
                continue

            normalized_px_username = normalize_plex_username(plex_username)
            discord_id = USER_MAPPINGS.get(normalized_px_username)

            if plex_username and discord_id:
                OVERSEERR_USERS_DATA[normalized_px_username] = {
                    "discord_id": discord_id,
                    "original_plex_username": plex_username
                }
        logger.info(
            f"Successfully synced {len(OVERSEERR_USERS_DATA)} Overseerr users.")

    except requests.exceptions.HTTPError as e:
        logger.error(
            f"HTTP Error fetching Overseerr users (Status: {e.response.status_code}): {e}")
        logger.error(f"Response body for HTTP error: {e.response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Network or request error fetching Overseerr users: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during Overseerr user sync: {e}", exc_info=True)


def get_discord_user_ids_for_tags(media_tags: list) -> set:
    """
    Returns a set of Discord user IDs to notify based on matching Sonarr tags.
    Matches if a user's normalized plexUsername is a substring of a normalized media tag.
    """
    users_to_notify = set()
    normalized_media_tags = [tag.lower() for tag in media_tags]

    for normalized_plex_username, user_data in OVERSEERR_USERS_DATA.items():
        if user_data.get("discord_id"):
            for media_tag in normalized_media_tags:
                if normalized_plex_username in media_tag:
                    users_to_notify.add(user_data["discord_id"])
                    break

    logger.debug(f"Users to notify for tags {media_tags}: {users_to_notify}")
    return users_to_notify


async def send_discord_notification(bot_instance, user_ids: set, message: str, channel_id: str):
    """Sends a message to a channel and/or DMs users."""
    if not bot_instance:
        logger.error(
            "Discord bot instance not passed to send_discord_notification. Cannot send messages.")
        return

    if channel_id:
        try:
            channel_id_int = int(channel_id)
            channel = bot_instance.get_channel(channel_id_int)
            if channel:
                logger.info(
                    f"Sending notification to channel {channel_id_int}.")
                await channel.send(message)
            else:
                logger.warning(
                    f"Could not find notification channel with ID: {channel_id}")
        except ValueError:
            logger.error(
                f"Invalid notification_channel_id: {channel_id}. Must be an integer.")

    if DM_NOTIFICATIONS_ENABLED:
        for user_id_str in user_ids:
            try:
                user_id_int = int(user_id_str)
                user = await bot_instance.fetch_user(user_id_int)
                logger.info(
                    f"Attempting to DM user {user.name} ({user_id_int}).")
                await user.send(message)
            except ValueError:
                logger.warning(
                    f"Discord user ID {user_id_str} is not a valid integer. Skipping DM.")
            except discord.NotFound:
                logger.warning(
                    f"Discord user with ID {user_id_str} not found.")
            except discord.Forbidden:
                logger.warning(
                    f"Could not DM user {user_id_str}. They might have DMs disabled.")
            except Exception as e:
                logger.error(f"Error sending DM to {user_id_str}: {e}")

# --- Webhook Endpoints ---


@app.route('/webhook/sonarr', methods=['POST'])
async def sonarr_webhook():
    payload = request.json
    logger.info(
        f"Received Sonarr webhook: {payload.get('eventType')} from {request.remote_addr}")
    logger.debug(f"Sonarr webhook payload: {json.dumps(payload, indent=2)}")

    event_type = payload.get('eventType')

    if event_type not in ['Download', 'Episode Imported']:
        logger.info(
            f"Sonarr event type '{event_type}' not handled. Ignoring.")
        return jsonify({"status": "ignored", "message": f"Event type '{event_type}' not handled"}), 200

    series = payload.get('series', {})
    episodes = payload.get('episodes', [])
    release = payload.get('release', {})

    if not series or not episodes:
        logger.warning(
            "Sonarr webhook payload missing series or episodes data.")
        return jsonify({"status": "error", "message": "Missing series or episode data"}), 400

    series_title = series.get('title')
    series_id = series.get('id')
    series_tags = series.get('tagsArray', [])

    users_to_ping = get_discord_user_ids_for_tags(series_tags)

    if not users_to_ping:
        logger.info(
            f"No users found for Sonarr event tags: {series_tags} for series '{series_title}'.")
        return jsonify({"status": "no_users_matched", "message": "No users mapped to these tags"}), 200

    for episode in episodes:
        episode_id = episode.get('id')
        episode_number = episode.get('episodeNumber')
        season_number = episode.get('seasonNumber')
        episode_title = episode.get('title')

        episode_unique_id = (series_id, episode_id,
                             release.get('releaseTitle'))

        if episode_unique_id in NOTIFIED_EPISODES_CACHE:
            logger.info(
                f"Episode {series_title} S{season_number:02d}E{episode_number:02d} (Release: {release.get('releaseTitle')}) already notified. Skipping.")
            continue

        NOTIFIED_EPISODES_CACHE.append(episode_unique_id)

        mentions = " ".join([f"<@{uid}>" for uid in users_to_ping])

        notification_message = (
            f"🎉 {mentions} **New Episode Available!** 🎉\n"
            f"**Series:** {series_title}\n"
            f"**Episode:** S{season_number:02d}E{episode_number:02d} - {episode_title}\n"
            f"It's now available for streaming!"
        )

        if 'discord_bot' in app.config:
            await send_discord_notification(
                app.config['discord_bot'],
                users_to_ping,
                notification_message,
                NOTIFICATION_CHANNEL_ID
            )
            logger.info(
                f"Notification sent for {series_title} S{season_number:02d}E{episode_number:02d} to {len(users_to_ping)} users.")
        else:
            logger.error(
                "Discord bot instance not found in Flask app config. Cannot send notifications.")

    return jsonify({"status": "success", "message": "Webhook processed"}), 200

# --- Background Task for Overseerr User Sync ---


async def start_overseerr_user_sync(bot_instance):
    """Periodically syncs users from Overseerr."""
    while True:
        await fetch_overseerr_users()
        interval = OVERSEERR_CONFIG.get("refresh_interval_minutes", 60)
        logger.info(f"Next Overseerr user sync in {interval} minutes.")
        await asyncio.sleep(interval * 60)

# --- Flask Server Startup ---


def run_webhook_server(bot_instance):
    """Starts the Flask server in a separate thread/process."""
    app.config['discord_bot'] = bot_instance

    logger.info("Flask server attempting to start...")
    # Use 0.0.0.0 to listen on all interfaces within the Docker container
    # Port 5000 is common for Flask, ensure it's exposed in docker-compose.yml
    # Changed debug to False for production
    app.run(host='0.0.0.0', port=5000, debug=False)
    # This might not be reached if app.run blocks forever
    logger.info("Flask server stopped.")


async def setup_media_watcher_service(bot_instance):
    """Sets up the media watcher service, including webhook server and sync task."""
    logger.info("Setting up Media Watcher Service...")

    # Initial sync
    await fetch_overseerr_users()

    # Start the periodic Overseerr user sync task
    bot_instance.loop.create_task(start_overseerr_user_sync(bot_instance))

    # Start the Flask webhook server in a separate thread/process
    import threading
    thread = threading.Thread(target=run_webhook_server, args=(bot_instance,))
    thread.daemon = True  # Allows the thread to exit when the main program exits
    thread.start()
    logger.info(
        "Flask webhook server started in a separate thread on port 5000.")
