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


async def fetch_tmdb_movie_details(tmdb_id: int, api_key: str) -> dict:
    """
    Fetches detailed movie information from the TMDB API, including videos and release dates.
    """
    if not api_key:
        logger.warning("TMDB API key is not configured. Skipping TMDB fetch.")
        return {}

    # **CHANGE**: Added 'release_dates' to get MPAA rating (certification)
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?append_to_response=videos,release_dates"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(
            f"Error fetching details from TMDB for movie {tmdb_id}: {e}")

    return {}


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
    logger.debug(
        f"Starting to process buffered notifications for series_id: {series_id}.")

    buffered_items = EPISODE_NOTIFICATION_BUFFER.pop(series_id, [])
    if series_id in SERIES_NOTIFICATION_TIMERS:
        del SERIES_NOTIFICATION_TIMERS[series_id]
        logger.debug(f"Removed timer for series_id: {series_id}.")

    if not buffered_items:
        logger.info(
            f"Buffer for series ID {series_id} was empty. No action needed.")
        return

    logger.debug(
        f"Processing {len(buffered_items)} buffered items for series_id: {series_id}.")

    # Add all processed episode unique IDs to the global cache
    for item in buffered_items:
        episode_unique_id = item.get("episode_unique_id", "N/A")
        if episode_unique_id not in NOTIFIED_EPISODES_CACHE:
            NOTIFIED_EPISODES_CACHE.append(episode_unique_id)
            logger.debug(
                f"Added {episode_unique_id} to NOTIFIED_EPISODES_CACHE.")
        else:
            logger.debug(
                f"{episode_unique_id} was already in NOTIFIED_EPISODES_CACHE.")

    # Use the most recent episode for the main embed details
    latest_item = buffered_items[-1]
    logger.debug(f"Latest item for main embed: {latest_item}")

    main_episode_data = latest_item.get('episode_data', {})
    series_data = latest_item.get('series_data_ref', {})
    release_data_for_main_ep = latest_item.get('release_data_ref', {})
    ep_file_for_main_ep = latest_item.get('specific_episode_file_info', {})

    series_title = series_data.get('title', "Unknown Series")
    series_year = series_data.get('year')
    season_number = main_episode_data.get('seasonNumber', 0)
    episode_number = main_episode_data.get('episodeNumber', 0)
    episode_title = main_episode_data.get('title', "Unknown Episode")
    episode_overview = main_episode_data.get(
        'overview', "No overview available.")
    air_date_utc_str = main_episode_data.get('airDateUtc')

    logger.debug(
        f"Extracted main episode details: Series='{series_title}', Episode='S{season_number:02d}E{episode_number:02d} - {episode_title}'")

    # --- Quality Extraction ---
    quality_string = "N/A"
    logger.debug("Starting quality extraction process...")
    if ep_file_for_main_ep and isinstance(ep_file_for_main_ep, dict):
        logger.debug(f"Processing ep_file_for_main_ep: {ep_file_for_main_ep}")
        custom_formats_list = ep_file_for_main_ep.get('customFormats', [])
        custom_formats_str = f" ({', '.join(custom_formats_list)})" if custom_formats_list else ""

        q_obj = ep_file_for_main_ep.get('quality', {})
        if isinstance(q_obj, str):
            quality_string = q_obj + custom_formats_str
        elif isinstance(q_obj, dict):
            base_quality_info = q_obj.get('quality', {})
            quality_name = base_quality_info.get('name')
            if quality_name:
                quality_string = quality_name
            if not custom_formats_str:  # Check nested
                nested_custom_formats = q_obj.get('customFormats', [])
                if nested_custom_formats:
                    custom_formats_str = f" ({', '.join(nested_custom_formats)})"
            if quality_string != "N/A" or custom_formats_str:
                quality_string = quality_string + \
                    custom_formats_str if quality_string != "N/A" else custom_formats_str.strip(
                        " ()")

    if quality_string == "N/A" and release_data_for_main_ep:
        logger.debug(
            f"Falling back to release_data for quality. Data: {release_data_for_main_ep}")
        quality_name_from_release = release_data_for_main_ep.get('quality')
        if quality_name_from_release:
            quality_string = quality_name_from_release
        custom_formats_from_release = release_data_for_main_ep.get(
            'customFormats', [])
        if custom_formats_from_release:
            cf_release_str = f" ({', '.join(custom_formats_from_release)})"
            quality_string = (quality_string + cf_release_str) if quality_string not in [
                "N/A", ""] else cf_release_str.strip(" ()")

    logger.debug(f"Final quality string: '{quality_string}'")
    # --- End Quality Extraction ---

    embed_title_str = f"{series_title}"
    if series_year:
        embed_title_str += f" ({series_year})"
    embed_title_str += f" (S{season_number:02d}E{episode_number:02d})"

    final_embed = discord.Embed(
        title=embed_title_str, color=0x5dadec)

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
        episode_overview = episode_overview[:253] + "..."
    final_embed.add_field(name="Overview (Latest)",
                          value=episode_overview if episode_overview else "N/A", inline=False)

    if air_date_utc_str:
        try:
            air_date = datetime.fromisoformat(
                air_date_utc_str.replace('Z', '+00:00'))
            final_embed.add_field(
                name="Air Date", value=air_date.strftime('%m/%d/%Y'), inline=True)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Could not parse airDateUtc: '{air_date_utc_str}'. Error: {e}")
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
        logger.debug(f"Set thumbnail to poster URL: {poster_url}")
    else:
        logger.debug("No poster image found for series.")

    fanart_url = next((img.get('remoteUrl') or img.get('url')
                      for img in main_episode_data.get('images', [])), None)
    if not fanart_url:
        fanart_url = next((img.get('remoteUrl') or img.get(
            'url') for img in series_images if img.get('coverType') == 'fanart'), None)

    if fanart_url:
        final_embed.set_image(url=fanart_url)
        logger.debug(f"Set image to fanart URL: {fanart_url}")
    else:
        logger.debug("No fanart image found for episode or series.")

    if len(buffered_items) > 1:
        sorted_items = sorted(buffered_items, key=lambda x: (
            x['episode_data'].get('seasonNumber', 0), x['episode_data'].get('episodeNumber', 0)))
        other_episodes_strs = [f"S{item['episode_data'].get('seasonNumber', 0):02d}E{item['episode_data'].get('episodeNumber', 0):02d} - {item['episode_data'].get('title', 'N/A')}"
                               for item in sorted_items if item['episode_data'].get('id') != main_episode_data.get('id')]
        if other_episodes_strs:
            other_eps_text = "\n".join(other_episodes_strs)
            if len(other_eps_text) > 1024:
                other_eps_text = other_eps_text[:1020] + "..."
            final_embed.add_field(
                name=f"Also Added ({len(other_episodes_strs)})", value=other_eps_text, inline=False)
            logger.debug(
                f"Added field for {len(other_episodes_strs)} other episodes.")

    final_embed.set_footer(
        text=f"{len(buffered_items)} episode(s) in this batch notification.")
    final_embed.timestamp = datetime.utcnow()

    # --- New Code with Error Handling ---
    users_to_ping = []
    mentions_text = ""
    try:
        logger.debug("Attempting to get Discord user IDs for tags...")
        tags = series_data.get('tags', [])
        logger.debug(f"Found tags: {tags}")
        if tags:
            users_to_ping = get_discord_user_ids_for_tags(tags)
            logger.debug(f"Successfully found users to ping: {users_to_ping}")
            mentions_text = " ".join(
                [f"<@{uid}>" for uid in users_to_ping]) if users_to_ping else ""
        else:
            logger.debug("No tags found in series data. Skipping user ping.")

    except Exception as e:
        logger.error(
            f"CRITICAL: Failed during get_discord_user_ids_for_tags call. Notification will be sent without a user ping. Error: {e}",
            exc_info=True
        )
    # --- End of New Code ---

    try:
        logger.debug("Attempting to send final Discord notification.")
        await send_discord_notification(
            bot_instance=bot_instance,
            user_ids=users_to_ping,
            message_content=mentions_text,
            channel_id=channel_id,
            embed=final_embed
        )
        logger.info(
            f"Successfully sent DEBOUNCED Discord notification for {series_title}, {len(buffered_items)} episode(s).")
    except Exception as e:
        logger.error(
            f"FATAL: Failed to send Discord notification for {series_title}.", exc_info=True)


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


@app.route('/webhook/radarr', methods=['POST'])
async def radarr_webhook_detailed():
    payload = request.json
    logger.info(
        f"Received DETAILED Radarr webhook: {payload.get('eventType')} from {request.remote_addr}")
    bot_instance = app.config.get('discord_bot')
    tmdb_api_key = config.get("tmdb", {}).get("api_key")

    if not bot_instance:
        logger.error("Discord bot instance not found.")
        return jsonify({"status": "error", "message": "Bot instance not configured"}), 500

    event_type = payload.get('eventType')
    if event_type not in ['Download', 'Grab']:
        return jsonify({"status": "ignored", "message": "Event type not handled"}), 200

    movie_data = payload.get('movie', {})
    movie_file_data = payload.get('movieFile', {})
    if not movie_data or not movie_file_data:
        return jsonify({"status": "error", "message": "Missing movie or movieFile data"}), 400

    tmdb_id = movie_data.get('tmdbId')
    release_identifier = movie_file_data.get(
        'sceneName') or movie_file_data.get('relativePath', '')
    unique_key = (tmdb_id, release_identifier, 'detailed')

    if unique_key in NOTIFIED_MOVIES_CACHE:
        logger.info(f"Notification for {unique_key} already sent. Skipping.")
        return jsonify({"status": "ignored", "message": "Duplicate event"}), 200
    NOTIFIED_MOVIES_CACHE.append(unique_key)

    # --- Fetch ALL Details from TMDB ---
    tmdb_details = await fetch_tmdb_movie_details(tmdb_id, tmdb_api_key)

    # --- Data Extraction (TMDB is the primary source) ---
    movie_title = tmdb_details.get('title') or movie_data.get('title', 'N/A')
    year = movie_data.get('year', 'N/A')
    overview = tmdb_details.get('overview') or movie_data.get(
        'overview', 'No overview available.')

    # --- Data from Radarr (for file-specific info) ---
    quality = movie_file_data.get('quality', 'N/A')

    # --- Build the Embed ---
    embed = discord.Embed(
        title=f"{movie_title} ({year})",
        description=overview,
        color=0x00A67E
    )
    icon_url = bot_instance.user.avatar.url if bot_instance.user and bot_instance.user.avatar else None
    embed.set_author(name="Movie is ready! ✅", icon_url=icon_url)

    poster_path = tmdb_details.get('poster_path')
    if poster_path:
        embed.set_thumbnail(
            url=f"https://image.tmdb.org/t/p/w500{poster_path}")

    backdrop_path = tmdb_details.get('backdrop_path')
    if backdrop_path:
        embed.set_image(url=f"https://image.tmdb.org/t/p/w1280{backdrop_path}")

    # --- Conditionally Build Embed Fields using TMDB Data ---

    # Find US certification (MPAA rating) from the release_dates
    certification = 'N/A'
    if 'release_dates' in tmdb_details:
        for country in tmdb_details['release_dates']['results']:
            if country['iso_3166_1'] == 'US':
                # Find the first certification that is not empty
                cert_obj = next(
                    (rd for rd in country['release_dates'] if rd['certification']), None)
                if cert_obj:
                    certification = cert_obj['certification']
                    break

    runtime_min = tmdb_details.get('runtime', 0)

    # Combine ratings
    tmdb_vote = tmdb_details.get('vote_average', 0)
    # Get IMDb rating from Radarr's payload as TMDB doesn't provide it directly
    imdb_rating = movie_data.get('ratings', {}).get('imdb', {}).get('value', 0)

    # --- Add Fields to Embed ---
    if certification and certification != 'N/A':
        embed.add_field(name="Rating", value=certification, inline=True)

    embed.add_field(name="Quality", value=quality, inline=True)

    if runtime_min > 0:
        hours, minutes = divmod(runtime_min, 60)
        runtime_formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        embed.add_field(name="Runtime", value=runtime_formatted, inline=True)

    if tmdb_vote > 0 or imdb_rating > 0:
        ratings_parts = []
        if tmdb_vote > 0:
            ratings_parts.append(f"TMDB: {tmdb_vote:.1f}/10")
        if imdb_rating > 0:
            ratings_parts.append(f"IMDb: {imdb_rating}/10")
        embed.add_field(name="Ratings", value=" • ".join(
            ratings_parts), inline=False)

    # Add Trailer
    videos = tmdb_details.get('videos', {}).get('results', [])
    trailer = next((v for v in videos if v.get('site') ==
                   'YouTube' and v.get('type') == 'Trailer'), None)
    if trailer and trailer.get('key'):
        trailer_url = f"https://www.youtube.com/watch?v={trailer['key']}"
        embed.add_field(
            name="Trailer", value=f"[Watch on YouTube]({trailer_url})", inline=False)

    user_tags = movie_data.get('tags', [])
    user_ids_to_notify = get_discord_user_ids_for_tags(user_tags)
    requesting_user = get_requesting_user_from_tags(user_tags)
    mentions_text = " ".join(
        [f"<@{uid}>" for uid in user_ids_to_notify]) if user_ids_to_notify else None

    if requesting_user != "N/A":
        embed.add_field(name="Requested By",
                        value=requesting_user, inline=False)

    embed.timestamp = datetime.utcnow()

    logger.info(f"Sending TMDB-enriched notification for {movie_title}...")
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
