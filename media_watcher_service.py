"""
Media watcher service for the Plex Discord Bot.
"""
import asyncio
import logging
from collections import deque, defaultdict
from datetime import datetime
from flask import Flask, request, jsonify
from typing import Dict, Any, Set, Deque
import discord

from config import BotConfig
from media_watcher_utils import (
    fetch_tmdb_movie_details,
    get_discord_user_ids_for_tags,
    fetch_overseerr_users,
)

logger = logging.getLogger(__name__)

# --- State Management ---
EPISODE_NOTIFICATION_BUFFER: Dict[str, list] = defaultdict(list)
SERIES_NOTIFICATION_TIMERS: Dict[str, asyncio.TimerHandle] = {}
NOTIFIED_EPISODES_CACHE: Deque[tuple] = deque(maxlen=1000)
NOTIFIED_MOVIES_CACHE: Deque[tuple] = deque(maxlen=1000)
OVERSEERR_USERS_DATA: Dict[str, dict] = {}

DEBOUNCE_SECONDS = 60

app = Flask(__name__)

# --- Core Notification Logic ---


async def send_discord_notification(
    bot_instance: discord.Client,
    config: BotConfig,
    user_ids: Set[str],
    message_content: str,
    channel_id: str,
    embed: discord.Embed = None,
):
    """Sends a message with an optional embed to a channel and DMs users."""
    if not bot_instance:
        logger.error(
            "Discord bot instance not available. Cannot send messages.")
        return

    # Send to the main channel
    if channel_id:
        try:
            channel = bot_instance.get_channel(int(channel_id))
            if channel:
                await channel.send(content=message_content or None, embed=embed)
            else:
                logger.warning(
                    f"Could not find notification channel with ID: {channel_id}")
        except (ValueError, discord.HTTPException) as e:
            logger.error(
                f"Error sending message to channel {channel_id}: {e}", exc_info=True)

    # Send DMs
    if config.discord.dm_notifications_enabled and user_ids:
        for user_id in user_ids:
            try:
                user = await bot_instance.fetch_user(int(user_id))
                await user.send(content=message_content or None, embed=embed)
            except (ValueError, discord.NotFound, discord.Forbidden) as e:
                logger.warning(f"Could not send DM to user {user_id}: {e}")
            except Exception as e:
                logger.error(
                    f"Unexpected error sending DM to {user_id}: {e}", exc_info=True)


async def _process_and_send_buffered_notifications(series_id: str, bot_instance: discord.Client, channel_id: str):
    """Processes buffered episodes for a series and sends a single notification."""
    buffered_items = EPISODE_NOTIFICATION_BUFFER.pop(series_id, [])
    if series_id in SERIES_NOTIFICATION_TIMERS:
        SERIES_NOTIFICATION_TIMERS.pop(series_id).cancel()

    if not buffered_items:
        return

    # Mark as notified
    for item in buffered_items:
        NOTIFIED_EPISODES_CACHE.append(item.get("episode_unique_id", ""))

    # Use the first item for series metadata
    latest_item = buffered_items[-1]
    series_data = latest_item.get('series_data_ref', {})
    episode_data = latest_item.get('episode_data', {})
    quality_string = latest_item.get('quality', 'N/A')

    # --- Build Rich Embed (Sonarr) ---
    embed = discord.Embed(color=0x00A4DC)  # Sonarr Blue
    embed.set_author(name="New Episode Available - Sonarr",
                     icon_url="https://i.imgur.com/tV61XQZ.png")

    # Title
    season_num = episode_data.get('seasonNumber', 0)
    episode_num = episode_data.get('episodeNumber', 0)
    ep_string = f"S{season_num:02d}E{episode_num:02d}"

    embed.title = f"{series_data.get('title', 'Unknown Series')} ({series_data.get('year', 'N/A')}) ({ep_string})"

    # Episode Info
    episode_title = episode_data.get('title', 'Unknown Title')
    embed.add_field(name=f"Latest: {episode_title}",
                    value=ep_string, inline=False)

    # Overview
    overview = episode_data.get('overview', '')
    if overview:
        if len(overview) > 1000:
            overview = overview[:997] + "..."
        embed.add_field(name="Overview (Latest)", value=overview, inline=False)

    # Details
    air_date = episode_data.get('airDate', 'N/A')
    embed.add_field(name="Air Date", value=air_date, inline=True)
    embed.add_field(name="Quality", value=quality_string, inline=True)

    # Footer
    ep_count = len(buffered_items)
    timestamp_str = datetime.now().strftime("%H:%M")
    embed.set_footer(
        text=f"{ep_count} episode(s) in this batch notification. • Today at {timestamp_str}")

    # Images
    images = series_data.get('images', [])
    poster_url = None
    fanart_url = None

    for img in images:
        if img.get('coverType') == 'poster':
            poster_url = img.get('url')
        elif img.get('coverType') == 'fanart':
            fanart_url = img.get('url')

    if fanart_url:
        embed.set_image(url=fanart_url)
    if poster_url:
        embed.set_thumbnail(url=poster_url)

    # --- User Tagging ---
    user_tags = series_data.get('tags', [])
    users_to_ping = get_discord_user_ids_for_tags(user_tags)

    mentions_text = ""
    if users_to_ping:
        mentions_text = " ".join([f"<@{uid}>" for uid in users_to_ping])
        logger.info(
            f"Sonarr Notification: Tagging users {users_to_ping} based on tags {user_tags}")

    # Send Notification
    await send_discord_notification(
        bot_instance=bot_instance,
        config=bot_instance.config,
        user_ids=users_to_ping,
        message_content=mentions_text,
        channel_id=channel_id,
        embed=embed,
    )

# --- Webhook Endpoints ---


@app.route('/webhook/radarr', methods=['POST'])
async def radarr_webhook_detailed():
    """Handles Radarr's 'On Grab' and 'On Download' webhook events."""
    try:
        payload = request.json
        if not payload:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            return jsonify({"status": "error", "message": "Internal server error"}), 500

        config = bot_instance.config
        tmdb_api_key = config.tmdb.api_key

        if payload.get('eventType') not in ['Download', 'Grab']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        movie_data = payload.get('movie', {})
        movie_file_data = payload.get('movieFile', {})
        remote_movie_data = payload.get('remoteMovie', {})

        # Deduplication
        unique_key = (movie_data.get('tmdbId'), movie_file_data.get(
            'relativePath', 'unknown'), payload.get('eventType'))
        if unique_key in NOTIFIED_MOVIES_CACHE:
            return jsonify({"status": "ignored", "message": "Duplicate event"}), 200
        NOTIFIED_MOVIES_CACHE.append(unique_key)

        # Fetch TMDB Details
        tmdb_details = await fetch_tmdb_movie_details(movie_data.get('tmdbId'), tmdb_api_key)

        # --- Build Rich Embed for Radarr ---
        embed = discord.Embed(color=0xFFC107)  # Radarr Yellow/Gold
        embed.set_author(name=f"New Movie Available - Radarr",
                         icon_url="https://i.imgur.com/d1p8gCa.png")

        title = movie_data.get('title', 'Unknown Movie')
        year = movie_data.get('year', '')
        embed.title = f"{title} ({year})"

        # Overview
        overview = movie_data.get('overview', '')
        if not overview and tmdb_details:
            overview = tmdb_details.get('overview', '')

        if overview:
            if len(overview) > 1000:
                overview = overview[:997] + "..."
            embed.add_field(name="Overview", value=overview, inline=False)

        # Details
        quality = movie_file_data.get(
            'quality', remote_movie_data.get('quality', 'N/A'))
        embed.add_field(name="Quality", value=quality, inline=True)

        release_date = movie_data.get(
            'inCinemas') or movie_data.get('physicalRelease')
        if release_date:
            embed.add_field(name="Release Date",
                            value=release_date, inline=True)

        # Images (Prefer TMDB, fallback to payload)
        poster_path = tmdb_details.get('poster_path')
        backdrop_path = tmdb_details.get('backdrop_path')

        if backdrop_path:
            embed.set_image(
                url=f"https://image.tmdb.org/t/p/w1280{backdrop_path}")
        elif poster_path:
            embed.set_image(
                url=f"https://image.tmdb.org/t/p/w780{poster_path}")

        if poster_path:
            embed.set_thumbnail(
                url=f"https://image.tmdb.org/t/p/w300{poster_path}")

        # --- Tagging ---
        user_tags = movie_data.get('tags', [])
        user_ids_to_notify = get_discord_user_ids_for_tags(user_tags)
        mentions = ""
        if user_ids_to_notify:
            mentions = " ".join([f"<@{uid}>" for uid in user_ids_to_notify])
            logger.info(
                f"Radarr Notification: Tagging users {user_ids_to_notify} based on tags {user_tags}")

        # Construct notification coroutine
        coro = send_discord_notification(
            bot_instance,
            config,
            user_ids_to_notify,
            mentions,
            config.discord.radarr_notification_channel_id,
            embed
        )
        # Schedule it safely
        asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing Radarr webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/webhook/sonarr', methods=['POST'])
async def sonarr_webhook():
    """Handles Sonarr's 'On Grab' and 'On Download' webhook events with debouncing."""
    try:
        payload = request.json
        if not payload:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            return jsonify({"status": "error", "message": "Internal server error"}), 500

        config = bot_instance.config

        if payload.get('eventType') not in ['Download', 'EpisodeImport', 'Grab']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        series_data = payload.get('series', {})
        series_id = series_data.get('id')
        if not series_id:
            return jsonify({"status": "error", "message": "Missing series ID"}), 400

        # Extract Quality
        quality = "N/A"
        if 'episodeFile' in payload and 'quality' in payload['episodeFile']:
            quality = payload['episodeFile']['quality']
        elif 'release' in payload and 'quality' in payload['release']:
            quality = payload['release']['quality']

        for episode_data in payload.get('episodes', []):
            unique_id = (series_id, episode_data.get('id'))
            if unique_id in NOTIFIED_EPISODES_CACHE:
                continue

            EPISODE_NOTIFICATION_BUFFER[series_id].append({
                "episode_data": episode_data,
                "episode_unique_id": unique_id,
                "series_data_ref": series_data,
                "quality": quality
            })

        if series_id in SERIES_NOTIFICATION_TIMERS:
            SERIES_NOTIFICATION_TIMERS[series_id].cancel()

        # Define a safe callback function for call_later
        def schedule_notification():
            coro = _process_and_send_buffered_notifications(
                series_id,
                bot_instance,
                config.discord.sonarr_notification_channel_id
            )
            asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)

        # Schedule the debounce timer
        SERIES_NOTIFICATION_TIMERS[series_id] = bot_instance.loop.call_later(
            DEBOUNCE_SECONDS,
            schedule_notification
        )

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing Sonarr webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- Service Setup ---


async def start_overseerr_user_sync(bot_instance: discord.Client):
    """Periodically syncs users from Overseerr."""
    config = bot_instance.config
    while True:
        global OVERSEERR_USERS_DATA
        OVERSEERR_USERS_DATA = await fetch_overseerr_users()
        await asyncio.sleep(config.overseerr.refresh_interval_minutes * 60)


def run_webhook_server(bot_instance: discord.Client):
    """Starts the Flask server in a separate thread."""
    app.config['discord_bot'] = bot_instance
    app.run(host='0.0.0.0', port=5000, debug=False)


async def setup_media_watcher_service(bot_instance: discord.Client):
    """Initializes the media watcher service."""
    config = bot_instance.config
    if not config.discord.sonarr_notification_channel_id:
        logger.warning(
            "Sonarr notification channel not set. Sonarr notifications will be disabled.")
    if not config.discord.radarr_notification_channel_id:
        logger.warning(
            "Radarr notification channel not set. Radarr notifications will be disabled.")

    if config.overseerr.enabled:
        logger.info("Overseerr integration is enabled. Starting user sync.")
        bot_instance.loop.create_task(start_overseerr_user_sync(bot_instance))
    else:
        logger.info("Overseerr integration is disabled.")

    import threading
    threading.Thread(target=run_webhook_server, args=(
        bot_instance,), daemon=True).start()
    logger.info(
        "Flask webhook server started in a background thread on port 5000.")
