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
import asyncio
import logging
from collections import deque, defaultdict  # Add defaultdict
from datetime import datetime, timedelta  # Add timedelta


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

# --- Global Variables for Debouncing ---
EPISODE_NOTIFICATION_BUFFER = defaultdict(list)
# Stores { series_id: [episode_data_1, episode_data_2, ...], ... }

SERIES_NOTIFICATION_TIMERS = {}
# Stores { series_id: asyncio.TimerHandle, ... }

DEBOUNCE_SECONDS = 60  # configurable: e.g., 60 seconds to wait for more episodes


# Ensure required config sections exist
DISCORD_CONFIG = config.get("discord", {})
OVERSEERR_CONFIG = config.get("overseerr", {})
SONARR_INSTANCES = config.get("sonarr_instances", [])
USER_MAPPINGS = config.get("user_mappings", {}).get("plex_to_discord", {})

SONARR_NOTIFICATION_CHANNEL_ID = DISCORD_CONFIG.get(
    "sonarr_notification_channel_id")
RADARR_NOTIFICATION_CHANNEL_ID = DISCORD_CONFIG.get(
    "radarr_notification_channel_id")
DM_NOTIFICATIONS_ENABLED = DISCORD_CONFIG.get("dm_notifications_enabled", True)

if not SONARR_NOTIFICATION_CHANNEL_ID:
    logger.warning(
        "sonarr_notification_channel_id not set in config.json. Only DMs (if enabled) will work.")

if not RADARR_NOTIFICATION_CHANNEL_ID:
    logger.warning(
        "radarr_notification_channel_id not set in config.json. Only DMs (if enabled) will work.")

# --- Global State for User Data and De-duplication ---
OVERSEERR_USERS_DATA = {}
NOTIFIED_EPISODES_CACHE = deque(maxlen=1000)
NOTIFIED_MOVIES_CACHE = deque(maxlen=1000)

app = Flask(__name__)

# --- Helper Functions ---


def get_requesting_user_from_tags(media_tags: list) -> str:
    """
    Finds the first username in the media tags that matches a known user.
    """
    if not media_tags:
        return "N/A"

    normalized_media_tags = [str(tag).lower() for tag in media_tags]

    # Iterate through known users from your config's plex_to_discord mapping
    for plex_user_norm, discord_id in USER_MAPPINGS.items():
        # Check if the user's normalized name exists in any of the tags
        if any(plex_user_norm in tag for tag in normalized_media_tags):
            return plex_user_norm  # Return the first username that matches

    return "N/A"


async def _process_and_send_buffered_notifications(series_id, bot_instance, channel_id):
    logger.info(
        f"Timer expired for series ID {series_id}. Processing buffered notifications.")

    buffered_items = EPISODE_NOTIFICATION_BUFFER.pop(series_id, [])
    if series_id in SERIES_NOTIFICATION_TIMERS:
        del SERIES_NOTIFICATION_TIMERS[series_id]

    if not buffered_items:
        logger.info(f"Buffer for series ID {series_id} was empty. No action.")
        return

    # Add all processed episode unique IDs to the global cache now
    for item in buffered_items:
        if item["episode_unique_id"] not in NOTIFIED_EPISODES_CACHE:
            NOTIFIED_EPISODES_CACHE.append(item["episode_unique_id"])
            logger.debug(
                f"Added {item['episode_unique_id']} to NOTIFIED_EPISODES_CACHE.")
        else:
            logger.debug(
                f"{item['episode_unique_id']} was already in NOTIFIED_EPISODES_CACHE, re-confirming.")

    # Use data from the most recently added episode for the main embed details
    # Or sort by airdate/dateAdded if preferred
    latest_item = buffered_items[-1]
    main_episode_data = latest_item['episode_data']
    series_data = latest_item['series_data_ref']
    release_data_for_main_ep = latest_item['release_data_ref']
    # This is the matched file for the 'latest_item'
    ep_file_for_main_ep = latest_item.get('specific_episode_file_info')

    series_title = series_data.get('title', "Unknown Series")
    series_year = series_data.get('year')

    season_number = main_episode_data.get('seasonNumber', 0)
    episode_number = main_episode_data.get('episodeNumber', 0)
    episode_title = main_episode_data.get('title', "Unknown Episode")
    episode_overview = main_episode_data.get(
        'overview', "No overview available.")
    air_date_utc_str = main_episode_data.get('airDateUtc')

    # --- Quality Extraction for the main_episode_data (latest episode in batch) ---
    quality_string = "N/A"
    if ep_file_for_main_ep and isinstance(ep_file_for_main_ep, dict):
        custom_formats_list = ep_file_for_main_ep.get('customFormats')
        custom_formats_str = ""
        if custom_formats_list and isinstance(custom_formats_list, list) and custom_formats_list:
            custom_formats_str = f" ({', '.join(custom_formats_list)})"

        q_obj = ep_file_for_main_ep.get('quality')
        if isinstance(q_obj, str):
            quality_string = q_obj + custom_formats_str
        elif isinstance(q_obj, dict):  # Nested quality object
            base_quality_info = q_obj.get('quality')
            if base_quality_info and isinstance(base_quality_info, dict) and base_quality_info.get('name'):
                quality_string = base_quality_info.get('name')

            if not custom_formats_str:  # Check nested if not found at file level
                nested_custom_formats = q_obj.get('customFormats')
                if nested_custom_formats and isinstance(nested_custom_formats, list) and nested_custom_formats:
                    custom_formats_str = f" ({', '.join(nested_custom_formats)})"

            if quality_string != "N/A" or custom_formats_str:
                if quality_string == "N/A" and custom_formats_str:
                    quality_string = custom_formats_str.strip(" ()")
                elif quality_string != "N/A":
                    quality_string += custom_formats_str
        elif quality_string == "N/A" and custom_formats_str:  # Only custom formats at file level
            quality_string = custom_formats_str.strip(" ()")

    if quality_string == "N/A" and release_data_for_main_ep and isinstance(release_data_for_main_ep, dict):
        logger.debug(
            f"Falling back to release_data for quality for {series_title} S{season_number}E{episode_number}")
        quality_name_from_release = release_data_for_main_ep.get('quality')
        if quality_name_from_release:
            quality_string = quality_name_from_release

        custom_formats_from_release = release_data_for_main_ep.get(
            'customFormats')
        if custom_formats_from_release and isinstance(custom_formats_from_release, list) and custom_formats_from_release:
            cf_release_str = f" ({', '.join(custom_formats_from_release)})"
            if quality_string != "N/A" and quality_string != "":
                quality_string += cf_release_str
            elif quality_string == "N/A" and cf_release_str:
                quality_string = cf_release_str.strip(" ()")
    # --- End Quality Extraction ---

    embed_title_str = f"{series_title}"
    if series_year:
        embed_title_str += f" ({series_year})"
    embed_title_str += f" (S{season_number:02d}E{episode_number:02d})"

    # Distinct color for debounced
    final_embed = discord.Embed(
        title=embed_title_str, color=discord.Color.purple())

    author_name_prefix = "New Episode" if len(
        buffered_items) == 1 else f"{len(buffered_items)} New Episodes"
    if bot_instance.user and bot_instance.user.avatar:
        final_embed.set_author(
            name=f"{author_name_prefix} Available - Sonarr", icon_url=bot_instance.user.avatar.url)
    else:
        final_embed.set_author(name=f"{author_name_prefix} Available - Sonarr")

    final_embed.add_field(name="Latest: " + (episode_title if episode_title else "N/A"),
                          value=f"S{season_number:02d}E{episode_number:02d}", inline=False)
    if len(episode_overview) > 256:
        # Shorter for this style
        episode_overview = episode_overview[:253] + "..."
    final_embed.add_field(name="Overview (Latest)",
                          value=episode_overview if episode_overview else "N/A", inline=False)

    if air_date_utc_str:
        try:
            air_date = datetime.fromisoformat(
                air_date_utc_str.replace('Z', '+00:00'))
            final_embed.add_field(
                name="Air Date", value=air_date.strftime('%m/%d/%Y'), inline=True)
        except ValueError:
            final_embed.add_field(
                name="Air Date", value="N/A (unparseable)", inline=True)
    else:
        final_embed.add_field(name="Air Date", value="N/A", inline=True)
    final_embed.add_field(
        name="Quality", value=quality_string if quality_string else "N/A", inline=True)

    series_images = series_data.get('images', [])
    poster_url = next((img.get('remoteUrl') or img.get('url')
                      for img in series_images if img.get('coverType') == 'poster'), None)
    if poster_url:
        final_embed.set_thumbnail(url=poster_url)

    fanart_url = None
    main_ep_specific_file_info = latest_item.get(
        'specific_episode_file_info', {})
    # Images from the episode object in 'episodes' array
    main_ep_images_list = main_episode_data.get('images', [])

    # Prefer episode-specific image if available from Sonarr v4 payload structure (less common)
    if main_ep_images_list:
        ep_img_url = main_ep_images_list[0].get(
            'remoteUrl') or main_ep_images_list[0].get('url')
        if ep_img_url:
            fanart_url = ep_img_url

    if not fanart_url:  # Fallback to series fanart
        fanart_url = next((img.get('remoteUrl') or img.get(
            'url') for img in series_images if img.get('coverType') == 'fanart'), None)

    if fanart_url:
        final_embed.set_image(url=fanart_url)

    if len(buffered_items) > 1:
        other_episodes_strs = []
        # Sort by season/episode for listing
        sorted_buffered_items = sorted(buffered_items, key=lambda x: (
            x['episode_data'].get('seasonNumber', 0), x['episode_data'].get('episodeNumber', 0)))

        for item in sorted_buffered_items:
            # Only list if it's not the main one we already detailed, or if you want to list all
            if item['episode_data'].get('id') != main_episode_data.get('id'):
                s = item['episode_data'].get('seasonNumber', 0)
                e = item['episode_data'].get('episodeNumber', 0)
                t = item['episode_data'].get('title', "N/A")
                other_episodes_strs.append(f"S{s:02d}E{e:02d} - {t}")

        if other_episodes_strs:
            other_eps_text = "\n".join(other_episodes_strs)
            if len(other_eps_text) > 1000:  # Max field value length approx
                other_eps_text = other_eps_text[:1000] + "...\n(and more)"
            final_embed.add_field(
                name=f"Also Added ({len(other_episodes_strs)})", value=other_eps_text, inline=False)

    final_embed.set_footer(
        text=f"{len(buffered_items)} episode(s) in this batch notification.")
    final_embed.timestamp = datetime.utcnow()

    users_to_ping = get_discord_user_ids_for_tags(series_data.get('tags', []))
    mentions_text = " ".join(
        [f"<@{uid}>" for uid in users_to_ping]) if users_to_ping else ""

    try:
        await send_discord_notification(
            bot_instance=bot_instance,
            user_ids=users_to_ping,
            message_content=mentions_text,
            channel_id=channel_id,
            embed=final_embed
        )
        logger.info(
            f"Sent DEBOUNCED Discord notification for {series_title}, {len(buffered_items)} episode(s).")
    except Exception as e:
        logger.error(
            f"Error in _process_and_send_buffered_notifications when calling send_discord_notification: {e}", exc_info=True)


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


# --- Detailed Radarr Webhook Route ---

# --- Detailed Radarr Webhook Route (Corrected) ---

@app.route('/webhook/radarr', methods=['POST'])
async def radarr_webhook_detailed():
    payload = request.json
    logger.info(
        f"Received DETAILED Radarr webhook: {payload.get('eventType')} from {request.remote_addr}")
    logger.debug(f"Radarr webhook payload: {json.dumps(payload, indent=2)}")

    event_type = payload.get('eventType')
    bot_instance = app.config.get('discord_bot')

    if not bot_instance:
        logger.error("Discord bot instance not found. Cannot process webhook.")
        return jsonify({"status": "error", "message": "Bot instance not configured"}), 500

    if event_type not in ['Download', 'Grab']:
        return jsonify({"status": "ignored", "message": f"Event type '{event_type}' not handled"}), 200

    movie_data = payload.get('movie', {})
    release_data = payload.get('release', {})

    if not movie_data:
        logger.warning("Radarr webhook missing movie data.")
        return jsonify({"status": "error", "message": "Missing movie data"}), 400

    movie_id = movie_data.get('id')
    release_title = release_data.get('releaseTitle', '')
    unique_key = (movie_id, release_title, 'detailed')

    if unique_key in NOTIFIED_MOVIES_CACHE:
        logger.info(
            f"Detailed notification for movie event (key: {unique_key}) already sent. Skipping.")
        return jsonify({"status": "ignored", "message": "Duplicate event"}), 200

    NOTIFIED_MOVIES_CACHE.append(unique_key)

    movie_title = movie_data.get('title', 'N/A')
    year = movie_data.get('year', 'N/A')
    overview = movie_data.get('overview', 'No overview available.')
    quality = release_data.get('quality', 'N/A')
    certification = movie_data.get('certification', 'N/A')
    runtime_min = movie_data.get('runtime', 0)

    hours, minutes = divmod(runtime_min, 60)
    runtime_formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

    embed = discord.Embed(
        title=f"{movie_title} ({year})",
        description=overview,
        color=0x00A67E
    )

    # --- CORRECTED LINE IS HERE ---
    # We now check if bot_instance.user AND bot_instance.user.avatar exist before getting the URL.
    icon_url = bot_instance.user.avatar.url if bot_instance.user and bot_instance.user.avatar else None
    embed.set_author(name="Movie is ready! ✅", icon_url=icon_url)

    poster_url = next((img.get('remoteUrl') for img in movie_data.get(
        'images', []) if img.get('coverType') == 'poster'), None)
    if poster_url:
        embed.set_thumbnail(url=poster_url)

    fanart_url = next((img.get('remoteUrl') for img in movie_data.get(
        'images', []) if img.get('coverType') == 'fanart'), None)
    if fanart_url:
        embed.set_image(url=fanart_url)

    embed.add_field(name="Rating", value=certification, inline=True)
    embed.add_field(name="Quality", value=quality, inline=True)
    embed.add_field(name="Runtime", value=runtime_formatted, inline=True)

    ratings = movie_data.get('ratings', {})
    tmdb_rating = ratings.get('tmdb', {}).get('value', 0)
    imdb_rating = ratings.get('imdb', {}).get('value', 0)
    ratings_text = f"TMDB: {tmdb_rating}/10 • IMDb: {imdb_rating}/10"
    embed.add_field(name="Ratings", value=ratings_text, inline=False)

    embed.add_field(name="Release", value=f"`{release_title}`", inline=False)

    user_tags = movie_data.get('tags', [])
    user_ids_to_notify = get_discord_user_ids_for_tags(user_tags)
    requesting_user = get_requesting_user_from_tags(user_tags)
    mentions_text = " ".join(
        [f"<@{uid}>" for uid in user_ids_to_notify]) if user_ids_to_notify else None

    if requesting_user != "N/A":
        embed.add_field(name="Requested By",
                        value=requesting_user, inline=False)

    embed.timestamp = datetime.utcnow()

    logger.info(f"Sending notification for {movie_title}...")
    asyncio.run_coroutine_threadsafe(
        send_discord_notification(
            bot_instance=bot_instance,
            user_ids=user_ids_to_notify,
            message_content=mentions_text,
            channel_id=RADARR_NOTIFICATION_CHANNEL_ID,
            embed=embed
        ),
        bot_instance.loop
    )

    return jsonify({"status": "success", "message": "Detailed notification sent"}), 200


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
        embed.timestamp = datetime.now()

        if SONARR_NOTIFICATION_CHANNEL_ID:
            coro = send_discord_notification(
                bot_instance=bot_instance,
                user_ids=set(),
                message_content=None,  # No pings for a test message
                channel_id=SONARR_NOTIFICATION_CHANNEL_ID,
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
                "Discord sonarr_notification_channel_id not set; cannot send Sonarr Test notification.")

        return jsonify({"status": "success", "message": "Test webhook processed successfully"}), 200

    elif event_type in ['Download', 'Episode Imported']:
        series_data_from_payload = payload.get('series', {})
        episodes_payload_list = payload.get('episodes', [])
        release_data_from_payload = payload.get('release', {})

        all_episode_files_from_payload = payload.get('episodeFiles', [])
        singular_episode_file_from_payload = payload.get('episodeFile')

        if not series_data_from_payload or not episodes_payload_list:
            logger.warning("Webhook missing series or episodes data.")
            return jsonify({"status": "error", "message": "Missing series or episode data"}), 400

        series_id = series_data_from_payload.get('id')
        if not series_id:
            logger.warning("Webhook payload missing series ID. Cannot buffer.")
            return jsonify({"status": "error", "message": "Missing series ID"}), 400

        newly_buffered_count = 0
        for episode_data in episodes_payload_list:
            ep_id = episode_data.get('id')
            s_num = episode_data.get('seasonNumber')
            ep_num = episode_data.get('episodeNumber')

            current_episode_file_info_for_key = None
            if singular_episode_file_from_payload and len(episodes_payload_list) == 1:
                current_episode_file_info_for_key = singular_episode_file_from_payload
            elif all_episode_files_from_payload:
                for ef_data in all_episode_files_from_payload:
                    path_segment_to_match = f"S{s_num:02d}E{ep_num:02d}"
                    if (ef_data.get('relativePath') and path_segment_to_match in ef_data['relativePath']) or \
                       (ef_data.get('sceneName') and path_segment_to_match in ef_data['sceneName']):
                        current_episode_file_info_for_key = ef_data
                        break
                if not current_episode_file_info_for_key and all_episode_files_from_payload:
                    # Fallback if specific match fails but files are present (e.g. take first if relevant)
                    # This might need adjustment based on how Sonarr structures 'episodeFiles' for multi-episode 'episodes' lists
                    pass  # current_episode_file_info_for_key remains None or use a general one

            unique_key_parts = [series_id, ep_id]
            if current_episode_file_info_for_key and current_episode_file_info_for_key.get('relativePath'):
                unique_key_parts.append(
                    current_episode_file_info_for_key.get('relativePath'))
            elif release_data_from_payload.get('releaseTitle'):
                unique_key_parts.append(
                    release_data_from_payload.get('releaseTitle'))
            elif current_episode_file_info_for_key and current_episode_file_info_for_key.get('sceneName'):
                unique_key_parts.append(
                    current_episode_file_info_for_key.get('sceneName'))

            episode_unique_id = tuple(unique_key_parts)

            if episode_unique_id in NOTIFIED_EPISODES_CACHE:
                logger.info(
                    f"Episode S{s_num:02d}E{ep_num:02d} (key: {episode_unique_id}) already in global NOTIFIED_EPISODES_CACHE. Skipping for buffer.")
                continue

            already_in_buffer = False
            for buffered_item_check in EPISODE_NOTIFICATION_BUFFER.get(series_id, []):
                if buffered_item_check["episode_unique_id"] == episode_unique_id:
                    already_in_buffer = True
                    break
            if already_in_buffer:
                logger.debug(
                    f"Episode S{s_num:02d}E{ep_num:02d} (key: {episode_unique_id}) already in current buffer for series {series_id}. Skipping.")
                continue

            buffered_item = {
                "episode_data": dict(episode_data),
                "episode_unique_id": episode_unique_id,
                "series_data_ref": series_data_from_payload,
                "release_data_ref": release_data_from_payload,
                "specific_episode_file_info": dict(current_episode_file_info_for_key) if current_episode_file_info_for_key else None,
            }
            EPISODE_NOTIFICATION_BUFFER[series_id].append(buffered_item)
            newly_buffered_count += 1
            logger.debug(
                f"Buffered episode S{s_num:02d}E{ep_num:02d} for series {series_id}. Key: {episode_unique_id}")

        if newly_buffered_count > 0:
            if series_id in SERIES_NOTIFICATION_TIMERS:
                SERIES_NOTIFICATION_TIMERS[series_id].cancel()
                logger.debug(
                    f"Cancelled existing timer for series ID {series_id}.")

            loop = bot_instance.loop
            timer_handle = loop.call_later(
                DEBOUNCE_SECONDS,
                lambda s_id=series_id, b_inst=bot_instance: asyncio.run_coroutine_threadsafe(
                    _process_and_send_buffered_notifications(
                        s_id, b_inst, SONARR_NOTIFICATION_CHANNEL_ID),
                    loop
                )
            )
            SERIES_NOTIFICATION_TIMERS[series_id] = timer_handle
            logger.info(
                f"Scheduled/Reset notification for series ID {series_id} in {DEBOUNCE_SECONDS}s. Buffered {newly_buffered_count} new episode(s) from this webhook. Total in buffer for series: {len(EPISODE_NOTIFICATION_BUFFER[series_id])}")
        else:
            logger.info(
                "No new episodes from this webhook to buffer after de-duplication checks.")

        return jsonify({"status": "success", "message": "Webhook data processed for buffering"}), 200

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
