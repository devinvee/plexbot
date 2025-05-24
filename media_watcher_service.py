import os
import json
import logging
from flask import Flask, request, jsonify
import asyncio
import requests
import re
from collections import deque
import discord
from datetime import datetime

# Import the shared utility function (assuming utils.py is in the same directory)
from utils import load_config

# Get a logger for this module. It will inherit its level from the root logger configured in bot.py.
logger = logging.getLogger(__name__)

# --- Configuration Loading ---
# This module still needs to load the configuration for its own settings,
# unrelated to the global logging level setup.
CONFIG_FILE = "config.json"
config = {}  # Initialize config #
try:
    config = load_config(CONFIG_FILE)
    # This log will use the logging configuration set up in bot.py
    logger.info("Configuration loaded successfully in media_watcher_service.")
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(
        f"Error loading configuration in media_watcher_service: {e}. Exiting.")
    exit(1)  # Exit if essential config cannot be loaded #
# --- END Configuration Loading ---

# --- Specific Logger Level Adjustments for this Module ---
# Adjust log levels for chatty libraries used specifically or heavily by this module.
# 'werkzeug' is for the Flask server logs.
# 'requests' can also be set here if this module requires a different verbosity for it,
# or this line can be removed if bot.py sets a global level for 'requests'.
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
# --- END Specific Logger Level Adjustments ---

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


async def send_discord_notification(bot_instance, user_ids: set, message_content: str, channel_id: str, embed: discord.Embed = None):
    """Sends a message with optional embed to a channel and/or DMs users."""
    if not bot_instance:
        logger.error("Discord bot instance not passed. Cannot send messages.")
        return

    # Send to the main channel if channel_id is provided
    if channel_id:
        try:
            channel_id_int = int(channel_id)
            channel = bot_instance.get_channel(channel_id_int)
            if channel:
                logger.info(
                    f"Sending notification with embed to channel {channel_id_int}.")
                await channel.send(content=message_content if message_content else None, embed=embed)
            else:
                logger.warning(
                    f"Could not find notification channel with ID: {channel_id}")
        except ValueError:
            logger.error(
                f"Invalid notification_channel_id: {channel_id}. Must be an integer.")
        except Exception as e:
            logger.error(
                f"Error sending message/embed to channel {channel_id}: {e}", exc_info=True)

    # Send DMs if enabled and there are users to notify
    if DM_NOTIFICATIONS_ENABLED and user_ids:
        dm_message_content = message_content  # Or a simplified version for DMs
        for user_id_str in user_ids:
            try:
                user_id_int = int(user_id_str)
                user = await bot_instance.fetch_user(user_id_int)
                logger.info(
                    f"Attempting to DM user {user.name} ({user_id_int}) with embed.")
                # For DMs, you might choose to send a simpler message or the full embed.
                # Here, we send the same content and embed as to the channel.
                await user.send(content=dm_message_content if dm_message_content else None, embed=embed)
            except ValueError:
                logger.warning(
                    f"Discord user ID {user_id_str} is not a valid integer for DM. Skipping.")
            except discord.NotFound:
                logger.warning(
                    f"Discord user with ID {user_id_str} not found for DM.")
            except discord.Forbidden:
                logger.warning(
                    f"Could not DM user {user_id_str}. They might have DMs disabled or bot lacks permission.")
            except Exception as e:
                logger.error(
                    f"Error sending DM to {user_id_str}: {e}", exc_info=True)

# --- Webhook Endpoints ---


# Add near the top of media_watcher_service.py if not already there

# ... (other parts of your media_watcher_service.py file) ...


@app.route('/webhook/sonarr', methods=['POST'])
async def sonarr_webhook():
    payload = request.json
    logger.info(
        f"Received Sonarr webhook: {payload.get('eventType')} from {request.remote_addr}")
    logger.debug(f"Sonarr webhook payload: {json.dumps(payload, indent=2)}")

    event_type = payload.get('eventType')
    bot_instance = app.config.get('discord_bot')

    if not bot_instance:
        logger.error(
            "Discord bot instance not found in Flask app config. Cannot process webhook.")
        return jsonify({"status": "error", "message": "Bot instance not configured"}), 500

    if event_type == "Test":
        logger.info("Sonarr Test webhook received and processed successfully!")
        test_notification_message = "Sonarr webhook test successful! Connectivity is confirmed."

        embed = discord.Embed(
            title="Sonarr Test Successful!",
            description="This confirms that your Plexbot is receiving webhooks from Sonarr correctly.",
            color=discord.Color.green()
        )
        if bot_instance.user and bot_instance.user.avatar:
            embed.set_author(name="Plexbot Notification Service",
                             icon_url=bot_instance.user.avatar.url)
        else:
            embed.set_author(name="Plexbot Notification Service")
        embed.timestamp = datetime.utcnow()

        if NOTIFICATION_CHANNEL_ID:
            coro = send_discord_notification(
                bot_instance=bot_instance,
                user_ids=set(),
                message_content=None,  # No pings for a test message
                channel_id=NOTIFICATION_CHANNEL_ID,
                embed=embed
            )
            future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
            try:
                future.result(timeout=10)
                logger.info("Discord notification for Sonarr Test completed.")
            except asyncio.TimeoutError:
                logger.error(
                    "Sending Discord notification for Sonarr Test timed out.")
            except Exception as e:
                logger.error(
                    f"Error running Sonarr Test Discord notification: {e}", exc_info=True)
        else:
            logger.warning(
                "Discord notification_channel_id not set; cannot send Sonarr Test notification.")

        return jsonify({"status": "success", "message": "Test webhook processed successfully"}), 200

    elif event_type in ['Download', 'Episode Imported']:
        series_data = payload.get('series', {})
        episodes_data = payload.get('episodes', [])  # This is a list
        release_data = payload.get('release', {})

        if not series_data or not episodes_data:
            logger.warning(
                "Sonarr webhook missing series or episodes data for Download/Import.")
            return jsonify({"status": "error", "message": "Missing series or episode data"}), 400

        # Process the first episode in the list
        episode_data = episodes_data[0]

        series_title = series_data.get('title', "Unknown Series")
        series_year = series_data.get('year')
        season_number = episode_data.get('seasonNumber', 0)
        episode_number = episode_data.get('episodeNumber', 0)
        episode_title = episode_data.get('title', "Unknown Episode")
        episode_overview = episode_data.get(
            'overview', "No overview available.")
        air_date_utc_str = episode_data.get('airDateUtc')

        # --- Corrected Quality Extraction Logic ---
        quality_string = "N/A"  # Initialize

        episode_file_list = payload.get('episodeFiles')
        episode_file_data = None

        if episode_file_list and isinstance(episode_file_list, list) and len(episode_file_list) > 0:
            episode_file_data = episode_file_list[0]
        elif payload.get('episodeFile'):  # Fallback for singular episodeFile object
            episode_file_data = payload.get('episodeFile')

        if episode_file_data and isinstance(episode_file_data, dict):
            logger.debug(
                f"Processing episode_file_data for quality: {episode_file_data}")

            custom_formats_list = episode_file_data.get('customFormats')
            custom_formats_str = ""
            if custom_formats_list and isinstance(custom_formats_list, list) and custom_formats_list:
                custom_formats_str = f" ({', '.join(custom_formats_list)})"

            q_from_file_obj = episode_file_data.get('quality')

            if isinstance(q_from_file_obj, str):
                quality_string = q_from_file_obj
                # Append custom formats if they were found at the episode_file_data level
                quality_string += custom_formats_str
            elif isinstance(q_from_file_obj, dict):  # Nested quality object
                quality_details = q_from_file_obj
                base_quality_info = quality_details.get('quality')
                if base_quality_info and isinstance(base_quality_info, dict) and base_quality_info.get('name'):
                    quality_string = base_quality_info.get('name')

                # Use custom_formats_str if already populated from episode_file_data.customFormats
                # Otherwise, check within the nested quality object
                if not custom_formats_str:  # Check only if not already found
                    nested_custom_formats = quality_details.get(
                        'customFormats')
                    if nested_custom_formats and isinstance(nested_custom_formats, list) and nested_custom_formats:
                        custom_formats_str = f" ({', '.join(nested_custom_formats)})"

                # Avoid "N/A (format)"
                if quality_string != "N/A" or custom_formats_str:
                    if quality_string == "N/A" and custom_formats_str:
                        # Use only custom formats if base quality is N/A and we have custom formats
                        quality_string = custom_formats_str.strip(" ()")
                    elif quality_string != "N/A":
                        quality_string += custom_formats_str
            # This case handles if 'quality' field is missing but 'customFormats' are present at file level
            elif quality_string == "N/A" and custom_formats_str:
                quality_string = custom_formats_str.strip(" ()")

        # Fallback to release_data if quality is still "N/A"
        if quality_string == "N/A" and release_data and isinstance(release_data, dict):
            logger.debug(
                "Falling back to release_data for quality information.")
            quality_name_from_release = release_data.get('quality')
            if quality_name_from_release:
                quality_string = quality_name_from_release

            custom_formats_from_release = release_data.get('customFormats')
            if custom_formats_from_release and isinstance(custom_formats_from_release, list) and custom_formats_from_release:
                cf_release_str = f" ({', '.join(custom_formats_from_release)})"
                if quality_string != "N/A" and quality_string != "":
                    quality_string += cf_release_str
                elif quality_string == "N/A" and cf_release_str:  # only custom formats found in release
                    quality_string = cf_release_str.strip(" ()")
        # --- End Corrected Quality Extraction Logic ---

        # --- Construct the Embed ---
        embed_title = f"{series_title}"
        if series_year:
            embed_title += f" ({series_year})"
        embed_title += f" (S{season_number:02d}E{episode_number:02d})"

        embed = discord.Embed(
            title=embed_title,
            color=discord.Color.green()
        )

        if bot_instance.user and bot_instance.user.avatar:
            embed.set_author(name="New Episode Available - Sonarr",
                             icon_url=bot_instance.user.avatar.url)
        else:
            embed.set_author(name="New Episode Available - Sonarr")

        embed.add_field(name="Episode Title",
                        value=episode_title if episode_title else "N/A", inline=False)

        if len(episode_overview) > 1020:
            episode_overview = episode_overview[:1020] + "..."
        embed.add_field(name=f"E{episode_number} Overview",
                        value=episode_overview, inline=False)

        if air_date_utc_str:
            try:
                air_date = datetime.fromisoformat(
                    air_date_utc_str.replace('Z', '+00:00'))
                embed.add_field(name="Air Date", value=air_date.strftime(
                    '%m/%d/%Y'), inline=True)
            except ValueError:
                logger.warning(
                    f"Could not parse airDateUtc: {air_date_utc_str}")
                # Add field with N/A only if airDateUtc was present but unparseable
                embed.add_field(name="Air Date",
                                value="N/A (unparseable)", inline=True)
        else:
            embed.add_field(name="Air Date", value="N/A", inline=True)

        embed.add_field(
            name="Quality", value=quality_string if quality_string else "N/A", inline=True)

        series_images = series_data.get('images', [])
        poster_url = None
        for img in series_images:
            if img.get('coverType') == 'poster' and img.get('remoteUrl'):  # Sonarr v4 uses remoteUrl
                poster_url = img.get('remoteUrl')
            # Older Sonarr might use url
            elif img.get('coverType') == 'poster' and img.get('url'):
                poster_url = img.get('url')
            if poster_url:
                break
        if poster_url:
            embed.set_thumbnail(url=poster_url)

        fanart_url = None
        for img in series_images:  # Check series images first
            if img.get('coverType') == 'fanart' and img.get('remoteUrl'):
                fanart_url = img.get('remoteUrl')
            elif img.get('coverType') == 'fanart' and img.get('url'):
                fanart_url = img.get('url')
            if fanart_url:
                break

        # Sonarr v4 episode objects might have an 'images' array for screenshots
        episode_images = episode_data.get('images', [])
        if episode_images:  # Prefer episode image if available
            if episode_images[0].get('remoteUrl'):
                fanart_url = episode_images[0].get('remoteUrl')
            elif episode_images[0].get('url'):
                fanart_url = episode_images[0].get('url')

        if fanart_url:
            embed.set_image(url=fanart_url)

        embed.timestamp = datetime.utcnow()  # Sets the footer timestamp

        # --- End Embed Construction ---

        users_to_ping = get_discord_user_ids_for_tags(
            # Ensure using tagsArray if that's what Sonarr sends
            series_data.get('tags', []))
        mentions_text = " ".join(
            [f"<@{uid}>" for uid in users_to_ping]) if users_to_ping else ""

        series_id_for_dedupe = series_data.get('id')
        # Corrected: this should be from episode_data
        episode_id_for_dedupe = episode_data.get('id')
        release_title_for_dedupe = release_data.get('releaseTitle')
        episode_unique_id = (series_id_for_dedupe,
                             episode_id_for_dedupe, release_title_for_dedupe)

        if episode_unique_id in NOTIFIED_EPISODES_CACHE:
            logger.info(
                f"Episode {series_title} S{season_number:02d}E{episode_number:02d} (Release: {release_title_for_dedupe}) already notified based on cache. Skipping.")
            return jsonify({"status": "success", "message": "Already notified, skipped"}), 200

        NOTIFIED_EPISODES_CACHE.append(episode_unique_id)

        if users_to_ping or NOTIFICATION_CHANNEL_ID:
            coro = send_discord_notification(
                bot_instance=bot_instance,
                user_ids=users_to_ping,
                message_content=mentions_text,
                channel_id=NOTIFICATION_CHANNEL_ID,
                embed=embed
            )
            future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
            try:
                future.result(timeout=10)
                logger.info(
                    f"Discord embed notification for {embed_title} sent successfully.")
            except asyncio.TimeoutError:
                logger.error(
                    f"Sending Discord embed notification for {embed_title} timed out.")
            except Exception as e:
                logger.error(
                    f"Error running Download/Import Discord embed notification: {e}", exc_info=True)
        else:
            logger.info(
                f"No users to ping and no notification channel ID for {embed_title}. No Discord notification sent.")

        return jsonify({"status": "success", "message": "Webhook processed"}), 200

    else:
        logger.info(
            f"Sonarr event type '{event_type}' is not explicitly handled. Ignoring.")
        return jsonify({"status": "ignored", "message": f"Event type '{event_type}' not handled"}), 200


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

    logger.info("Flask server attempting to start on host 0.0.0.0 port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False)
    logger.info("Flask server stopped.")

# This function will be called by bot.py


async def setup_media_watcher_service(bot_instance):
    """Sets up the media watcher service, including webhook server and sync task."""
    logger.info("Setting up Media Watcher Service...")

    # Initial sync of Overseerr users
    await fetch_overseerr_users()

    # Start the periodic Overseerr user sync task in the bot's event loop
    bot_instance.loop.create_task(start_overseerr_user_sync(bot_instance))

    # Start the Flask webhook server in a separate thread
    # This is crucial so it doesn't block the bot's asyncio loop
    import threading
    flask_thread = threading.Thread(
        target=run_webhook_server, args=(bot_instance,), daemon=True)
    flask_thread.start()
    logger.info(
        "Flask webhook server started in a separate daemon thread on port 5000.")
