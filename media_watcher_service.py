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
try:
    config = load_config(CONFIG_FILE)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading configuration: {e}")
    exit(1)

# Ensure required config sections exist
DISCORD_CONFIG = config.get("discord", {})
OVERSEERR_CONFIG = config.get("overseerr", {})
SONARR_INSTANCES = config.get("sonarr_instances", [])
# RADARR_INSTANCES is no longer in config.json, so we remove its reference
# RADARR_INSTANCES = config.get("radarr_instances", [])
USER_MAPPINGS = config.get("user_mappings", {}).get("plex_to_discord", {})
# TAG_TO_PLEX_USERS_MAP is no longer in config.json, so we remove its reference
# TAG_TO_PLEX_USERS_MAP = config.get("tag_to_plex_users_map", {})

NOTIFICATION_CHANNEL_ID = DISCORD_CONFIG.get("notification_channel_id")
DM_NOTIFICATIONS_ENABLED = DISCORD_CONFIG.get("dm_notifications_enabled", True)

if not NOTIFICATION_CHANNEL_ID:
    logging.warning(
        "Discord notification_channel_id not set in config.json. Only DMs (if enabled) will work.")

# --- Global State for User Data and De-duplication ---
# This will store {normalized_plex_username: {discord_id: "...", plex_username: "OriginalName"}}
OVERSEERR_USERS_DATA = {}
# Stores (series_id, episode_id, release_title) for de-duplication
# Max size 1000 to prevent memory growth, older entries fall off
NOTIFIED_EPISODES_CACHE = deque(maxlen=1000)

app = Flask(__name__)

# --- Helper Functions ---


def normalize_plex_username(username: str) -> str:
    """Converts a plex username to a consistent format for tag matching."""
    return username.lower().replace(" ", "")


async def fetch_overseerr_users():
    """Fetches users from Overseerr API and populates OVERSEERR_USERS_DATA."""
    if not OVERSEERR_CONFIG.get("base_url") or not OVERSEERR_CONFIG.get("api_key"):
        logging.warning("Overseerr API config missing. Skipping user sync.")
        return

    # Corrected endpoint
    url = f"{OVERSEERR_CONFIG['base_url'].rstrip('/')}/api/v1/user"
    # Added Accept header
    headers = {
        "X-Api-Key": OVERSEERR_CONFIG['api_key'], "Accept": "application/json"}
    logging.info(f"Attempting to fetch Overseerr users from: {url}")

    try:
        # Added timeout
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)

        # --- NEW LOGGING STATEMENTS & ERROR HANDLING ---
        logging.info(
            f"Overseerr API Response Status Code: {response.status_code}")
        logging.info(f"Overseerr API Response Headers: {response.headers}")
        # Log the raw text response before attempting JSON parsing
        logging.info(
            f"Overseerr API Raw Response Text (first 500 chars): {response.text[:500]}...")

        response.raise_for_status()  # This will raise an HTTPError for 4xx/5xx responses

        users = None  # Initialize users to None

        try:
            users = response.json()
            logging.info(
                f"Successfully parsed Overseerr API response as JSON.")
            logging.info(f"Type of parsed 'users' object: {type(users)}")
            # Log full content if it's small, otherwise just the first few items or a sample
            if isinstance(users, list) and len(users) > 0:
                logging.info(
                    f"Content of parsed 'users' (first 2 items): {users[:2]}")
            elif isinstance(users, dict):
                logging.info(f"Content of parsed 'users' (full dict): {users}")
            else:
                logging.info(
                    f"Content of parsed 'users' (unexpected type): {users}")

        except requests.exceptions.JSONDecodeError as e:
            logging.error(
                f"Failed to decode JSON response from Overseerr: {e}")
            logging.error(f"Full problematic response text: {response.text}")
            return  # Exit function if response is not JSON

        # Ensure 'users' is a list to iterate over
        if not isinstance(users, list):
            logging.warning(
                f"Overseerr API returned an unexpected type for users. Expected list, got {type(users)}. Attempting to wrap if it's a single dict.")
            if isinstance(users, dict):
                # If it's a single user object (which /api/v1/user could return for some Overseerr versions/configs)
                users = [users]
            else:
                logging.error(
                    "Overseerr API response is neither a list nor a dictionary. Cannot process.")
                return  # Cannot proceed if it's not a list or single dict

        # --- END NEW LOGGING STATEMENTS & ERROR HANDLING ---

        # Clear existing data before populating
        OVERSEERR_USERS_DATA.clear()
        for user in users:
            # Ensure 'user' is a dictionary before calling .get()
            if not isinstance(user, dict):
                logging.error(
                    f"Skipping unexpected item in Overseerr users list. Expected dict, got {type(user)}: {user}")
                continue  # Skip this item and continue with the next one

            # This is the line that was failing
            plex_username = user.get('plexUsername')

            # Check if this plex_username is in our static USER_MAPPINGS to get Discord ID
            normalized_px_username = normalize_plex_username(plex_username)
            discord_id = USER_MAPPINGS.get(normalized_px_username)

            if plex_username and discord_id:
                OVERSEERR_USERS_DATA[normalized_px_username] = {
                    "discord_id": discord_id,
                    "original_plex_username": plex_username
                }
        logging.info(
            f"Successfully synced {len(OVERSEERR_USERS_DATA)} Overseerr users.")

    except requests.exceptions.HTTPError as e:
        # Catch specific HTTP errors (like 401, 403, 500)
        logging.error(
            f"HTTP Error fetching Overseerr users (Status: {e.response.status_code}): {e}")
        logging.error(f"Response body for HTTP error: {e.response.text}")
    except requests.exceptions.RequestException as e:
        # Catch general request errors (like network issues, timeouts)
        logging.error(
            f"Network or request error fetching Overseerr users: {e}")
    except Exception as e:
        # Catch any other unexpected errors
        # exc_info to get full traceback
        logging.error(
            f"An unexpected error occurred during Overseerr user sync: {e}", exc_info=True)


def get_discord_user_ids_for_tags(media_tags: list) -> set:
    """
    Returns a set of Discord user IDs to notify based on matching Sonarr tags.
    Matches if a user's normalized plexUsername is a substring of a normalized media tag.
    """
    users_to_notify = set()
    normalized_media_tags = [tag.lower() for tag in media_tags]

    # Match based on substring of normalized plex usernames in tags (for "1 - devinvee" type tags)
    for normalized_plex_username, user_data in OVERSEERR_USERS_DATA.items():
        if user_data.get("discord_id"):
            for media_tag in normalized_media_tags:
                if normalized_plex_username in media_tag:  # Substring match
                    users_to_notify.add(user_data["discord_id"])
                    break  # Found a match for this user, move to next user

    return users_to_notify


async def send_discord_notification(bot_instance, user_ids: set, message: str, channel_id: str):
    """Sends a message to a channel and/or DMs users."""
    if not bot_instance:
        logging.error(
            "Discord bot instance not passed to send_discord_notification. Cannot send messages.")
        return

    # Send to main notification channel if configured
    if channel_id:
        try:
            channel_id_int = int(channel_id)
            channel = bot_instance.get_channel(channel_id_int)
            if channel:
                await channel.send(message)
            else:
                logging.warning(
                    f"Could not find notification channel with ID: {channel_id}")
        except ValueError:
            logging.error(
                f"Invalid notification_channel_id: {channel_id}. Must be an integer.")

    # Send DMs if enabled
    if DM_NOTIFICATIONS_ENABLED:
        for user_id_str in user_ids:  # user_ids are strings from config/env
            try:
                user_id_int = int(user_id_str)
                user = await bot_instance.fetch_user(user_id_int)
                await user.send(message)
            except ValueError:
                logging.warning(
                    f"Discord user ID {user_id_str} is not a valid integer. Skipping DM.")
            except discord.NotFound:
                logging.warning(
                    f"Discord user with ID {user_id_str} not found.")
            except discord.Forbidden:
                logging.warning(
                    f"Could not DM user {user_id_str}. They might have DMs disabled.")
            except Exception as e:
                logging.error(f"Error sending DM to {user_id_str}: {e}")

# --- Webhook Endpoints ---


@app.route('/webhook/sonarr', methods=['POST'])
async def sonarr_webhook():
    payload = request.json
    logging.info(
        f"Received Sonarr webhook: {payload.get('eventType')} from {request.remote_addr}")

    # No webhook secret for Sonarr as per config.json
    # Security relies on obscurity of the URL or external measures (e.g., reverse proxy auth).

    event_type = payload.get('eventType')

    # Only process 'Download' or 'Episode Imported' events for availability
    if event_type not in ['Download', 'Episode Imported']:
        logging.info(
            f"Sonarr event type '{event_type}' not handled. Ignoring.")
        return jsonify({"status": "ignored", "message": f"Event type '{event_type}' not handled"}), 200

    series = payload.get('series', {})
    episodes = payload.get('episodes', [])  # Sonarr can send multiple episodes
    release = payload.get('release', {})

    if not series or not episodes:
        logging.warning(
            "Sonarr webhook payload missing series or episodes data.")
        return jsonify({"status": "error", "message": "Missing series or episode data"}), 400

    series_title = series.get('title')
    series_id = series.get('id')
    series_tags = series.get('tagsArray', [])  # This contains tag names

    users_to_ping = get_discord_user_ids_for_tags(series_tags)

    if not users_to_ping:
        logging.info(
            f"No users found for Sonarr event tags: {series_tags} for series '{series_title}'.")
        return jsonify({"status": "no_users_matched", "message": "No users mapped to these tags"}), 200

    # Build notification message for each episode
    for episode in episodes:
        episode_id = episode.get('id')
        episode_number = episode.get('episodeNumber')
        season_number = episode.get('seasonNumber')
        episode_title = episode.get('title')

        # Unique identifier for de-duplication
        # Use release title as well as it indicates a new 'file' for the same episode
        episode_unique_id = (series_id, episode_id,
                             release.get('releaseTitle'))

        if episode_unique_id in NOTIFIED_EPISODES_CACHE:
            logging.info(
                f"Episode {series_title} S{season_number:02d}E{episode_number:02d} (Release: {release.get('releaseTitle')}) already notified. Skipping.")
            continue

        # Add to cache
        NOTIFIED_EPISODES_CACHE.append(episode_unique_id)

        # Craft the mention string based on users_to_ping
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
                users_to_ping,  # Pass the set of users for DM logic
                notification_message,
                NOTIFICATION_CHANNEL_ID
            )
            logging.info(
                f"Notification sent for {series_title} S{season_number:02d}E{episode_number:02d} to {len(users_to_ping)} users.")
        else:
            logging.error(
                "Discord bot instance not found in Flask app config. Cannot send notifications.")

    return jsonify({"status": "success", "message": "Webhook processed"}), 200

# Remove the entire /webhook/radarr endpoint as it's no longer in config.json
# @app.route('/webhook/radarr', methods=['POST'])
# async def radarr_webhook():
#    # ... (removed Radarr webhook logic) ...
#    return jsonify({"status": "success", "message": "Webhook processed"}), 200


# --- Background Task for Overseerr User Sync ---
async def start_overseerr_user_sync(bot_instance):
    """Periodically syncs users from Overseerr."""
    while True:
        await fetch_overseerr_users()
        interval = OVERSEERR_CONFIG.get("refresh_interval_minutes", 60)
        logging.info(f"Next Overseerr user sync in {interval} minutes.")
        await asyncio.sleep(interval * 60)  # Wait for the specified interval

# --- Flask Server Startup ---


def run_webhook_server(bot_instance):
    """Starts the Flask server in a separate thread/process."""
    app.config['discord_bot'] = bot_instance

    # Use 0.0.0.0 to listen on all interfaces within the Docker container
    # Port 5000 is common for Flask, ensure it's exposed in docker-compose.yml
    # debug=False for production
    app.run(host='0.0.0.0', port=5000, debug=False)

# This function will be called by bot.py


async def setup_media_watcher_service(bot_instance):
    """Sets up the media watcher service, including webhook server and sync task."""
    logging.info("Setting up Media Watcher Service...")

    # Initial sync
    await fetch_overseerr_users()

    # Start the periodic Overseerr user sync task
    bot_instance.loop.create_task(start_overseerr_user_sync(bot_instance))

    # Start the Flask webhook server in a separate thread/process
    import threading
    thread = threading.Thread(target=run_webhook_server, args=(bot_instance,))
    thread.daemon = True
    thread.start()
    logging.info(
        "Flask webhook server started in a separate thread on port 5000.")
