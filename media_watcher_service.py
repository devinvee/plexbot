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

# --- State Management (Globals, consider refactoring to a class) ---
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
        logger.error("Discord bot instance not available. Cannot send messages.")
        return

    # Send to the main channel
    if channel_id:
        try:
            channel = bot_instance.get_channel(int(channel_id))
            if channel:
                await channel.send(content=message_content or None, embed=embed)
            else:
                logger.warning(f"Could not find notification channel with ID: {channel_id}")
        except (ValueError, discord.HTTPException) as e:
            logger.error(f"Error sending message to channel {channel_id}: {e}", exc_info=True)

    # Send DMs
    if config.discord.dm_notifications_enabled and user_ids:
        for user_id in user_ids:
            try:
                user = await bot_instance.fetch_user(int(user_id))
                await user.send(content=message_content or None, embed=embed)
            except (ValueError, discord.NotFound, discord.Forbidden) as e:
                logger.warning(f"Could not send DM to user {user_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending DM to {user_id}: {e}", exc_info=True)

async def _process_and_send_buffered_notifications(series_id: str, bot_instance: discord.Client, channel_id: str):
    """Processes buffered episodes for a series and sends a single notification."""
    buffered_items = EPISODE_NOTIFICATION_BUFFER.pop(series_id, [])
    if series_id in SERIES_NOTIFICATION_TIMERS:
        SERIES_NOTIFICATION_TIMERS.pop(series_id).cancel()

    if not buffered_items:
        return

    for item in buffered_items:
        NOTIFIED_EPISODES_CACHE.append(item.get("episode_unique_id", ""))

    latest_item = buffered_items[-1]
    series_data = latest_item.get('series_data_ref', {})
    
    # Simplified embed creation logic
    embed = discord.Embed(
        title=f"{series_data.get('title', 'N/A')} ({series_data.get('year', 'N/A')})",
        color=0x5DADEB
    )
    # Further embed building would go here...

    user_tags = series_data.get('tags', [])
    users_to_ping = get_discord_user_ids_for_tags(user_tags)
    mentions_text = f"{series_data.get('title', 'N/A')} " + " ".join(
        f"<@{uid}>" for uid in users_to_ping
    )

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
            logger.error("Bot instance not found in Flask app config.")
            return jsonify({"status": "error", "message": "Internal server error"}), 500
        
        config = bot_instance.config
        tmdb_api_key = config.tmdb.api_key

        if payload.get('eventType') not in ['Download', 'Grab']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        movie_data = payload.get('movie', {})
        movie_file_data = payload.get('movieFile', {})
        unique_key = (movie_data.get('tmdbId'), movie_file_data.get('relativePath'), 'detailed')

        if unique_key in NOTIFIED_MOVIES_CACHE:
            return jsonify({"status": "ignored", "message": "Duplicate event"}), 200
        NOTIFIED_MOVIES_CACHE.append(unique_key)

        tmdb_details = await fetch_tmdb_movie_details(movie_data.get('tmdbId'), tmdb_api_key)
        # ... (rest of the Radarr embed creation)
        
        user_tags = movie_data.get('tags', [])
        user_ids_to_notify = get_discord_user_ids_for_tags(user_tags)
        mentions = " ".join(f"<@{uid}>" for uid in user_ids_to_notify)

        asyncio.run_coroutine_threadsafe(
            send_discord_notification(
                bot_instance, config, user_ids_to_notify, mentions, config.discord.radarr_notification_channel_id, discord.Embed()
            ),
            bot_instance.loop,
        )
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
            logger.error("Bot instance not found in Flask app config.")
            return jsonify({"status": "error", "message": "Internal server error"}), 500

        config = bot_instance.config

        if payload.get('eventType') not in ['Download', 'EpisodeImport']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        series_data = payload.get('series', {})
        series_id = series_data.get('id')
        if not series_id:
            return jsonify({"status": "error", "message": "Missing series ID"}), 400

        for episode_data in payload.get('episodes', []):
            unique_id = (series_id, episode_data.get('id'))
            if unique_id in NOTIFIED_EPISODES_CACHE:
                continue
            
            EPISODE_NOTIFICATION_BUFFER[series_id].append({
                "episode_data": episode_data,
                "episode_unique_id": unique_id,
                "series_data_ref": series_data,
            })

        if series_id in SERIES_NOTIFICATION_TIMERS:
            SERIES_NOTIFICATION_TIMERS[series_id].cancel()

        SERIES_NOTIFICATION_TIMERS[series_id] = bot_instance.loop.call_later(
            DEBOUNCE_SECONDS,
            lambda: asyncio.run_coroutine_threadsafe(
                _process_and_send_buffered_notifications(series_id, bot_instance, config.discord.sonarr_notification_channel_id),
                bot_instance.loop,
            ),
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
        logger.warning("Sonarr notification channel not set. Sonarr notifications will be disabled.")
    if not config.discord.radarr_notification_channel_id:
        logger.warning("Radarr notification channel not set. Radarr notifications will be disabled.")

    if config.overseerr.enabled:
        logger.info("Overseerr integration is enabled. Starting user sync.")
        bot_instance.loop.create_task(start_overseerr_user_sync(bot_instance))
    else:
        logger.info("Overseerr integration is disabled.")

    import threading
    threading.Thread(target=run_webhook_server, args=(bot_instance,), daemon=True).start()
    logger.info("Flask webhook server started in a background thread on port 5000.")