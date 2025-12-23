"""
Media watcher service for the Plex Discord Bot.
"""
import asyncio
import logging
from collections import deque, defaultdict
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from typing import Dict, Any, Set, Deque
import discord

from config import BotConfig
from media_watcher_utils import (
    fetch_tmdb_movie_details,
    get_discord_user_ids_for_tags,
    fetch_overseerr_users,
)
from plex_utils import scan_plex_library_async

logger = logging.getLogger(__name__)

# --- State Management ---
EPISODE_NOTIFICATION_BUFFER: Dict[str, list] = defaultdict(list)
SERIES_NOTIFICATION_TIMERS: Dict[str, asyncio.TimerHandle] = {}
NOTIFIED_EPISODES_CACHE: Deque[tuple] = deque(maxlen=1000)
NOTIFIED_MOVIES_CACHE: Deque[tuple] = deque(maxlen=1000)
OVERSEERR_USERS_DATA: Dict[str, dict] = {}
NOTIFICATION_HISTORY: Deque[Dict[str, Any]] = deque(maxlen=100)  # Store last 100 notifications

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
                logger.info(f"Sent notification to channel {channel_id}")
            else:
                logger.warning(
                    f"Could not find notification channel with ID: {channel_id}. Is the bot in the server?")
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
    # Added broad try/except block to catch silent failures in background tasks
    try:
        logger.info(
            f"Processing buffered notifications for series_id: {series_id}")
        buffered_items = EPISODE_NOTIFICATION_BUFFER.pop(series_id, [])
        if series_id in SERIES_NOTIFICATION_TIMERS:
            SERIES_NOTIFICATION_TIMERS.pop(series_id, None)

        if not buffered_items:
            logger.warning(
                f"No buffered items found for series_id: {series_id}")
            return

        # Mark as notified
        for item in buffered_items:
            NOTIFIED_EPISODES_CACHE.append(item.get("episode_unique_id", ""))

        # Use the first item for series metadata
        latest_item = buffered_items[-1]
        series_data = latest_item.get('series_data_ref', {})
        episode_data = latest_item.get('episode_data', {})
        quality_string = latest_item.get('quality', 'N/A')
        series_path = series_data.get('path')  # Extract series path for Plex scanning

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
        embed.add_field(
            name=f"Latest: {episode_title}", value=ep_string, inline=False)

        # Overview
        overview = episode_data.get('overview', '')
        if overview:
            if len(overview) > 1000:
                overview = overview[:997] + "..."
            embed.add_field(name="Overview (Latest)",
                            value=overview, inline=False)

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
        # UPDATED: Validation logic to prefer remoteUrl and ensure HTTP
        images = series_data.get('images', [])
        poster_url = None
        fanart_url = None

        for img in images:
            # Prioritize 'remoteUrl' (TVDB link) over 'url' (often local path)
            url_to_use = img.get('remoteUrl')
            if not url_to_use:
                url_to_use = img.get('url')

            if img.get('coverType') == 'poster':
                poster_url = url_to_use
            elif img.get('coverType') == 'fanart':
                fanart_url = url_to_use
            elif img.get('coverType') == 'banner':
                if not fanart_url:
                    fanart_url = url_to_use

        # Only set if it looks like a valid http URL
        if fanart_url and fanart_url.startswith("http"):
            embed.set_image(url=fanart_url)
        elif fanart_url:
            logger.warning(f"Skipping invalid/local fanart URL: {fanart_url}")

        if poster_url and poster_url.startswith("http"):
            embed.set_thumbnail(url=poster_url)
        elif poster_url:
            logger.warning(f"Skipping invalid/local poster URL: {poster_url}")

        # --- User Tagging ---
        user_tags = series_data.get('tags', [])
        users_to_ping = get_discord_user_ids_for_tags(user_tags)

        mentions_text = ""
        if users_to_ping:
            ping_string = " ".join([f"<@{uid}>" for uid in users_to_ping])

            title = series_data.get('title', 'Unknown Series')
            mentions_text = f"Episode **{ep_string}** of **{title}** is now available! {ping_string}"
            logger.info(
                f"Sonarr Notification: Tagging users {users_to_ping} based on tags {user_tags}")

        # Send Notification
        logger.info("Sending Sonarr embed to Discord...")
        await send_discord_notification(
            bot_instance=bot_instance,
            config=bot_instance.config,
            user_ids=users_to_ping,
            message_content=mentions_text,
            channel_id=channel_id,
            embed=embed,
        )
        
        # Record notification in history
        NOTIFICATION_HISTORY.append({
            'type': 'sonarr',
            'title': series_data.get('title', 'Unknown Series'),
            'episode': {
                'season': season_num,
                'number': episode_num,
                'title': episode_title
            },
            'quality': quality_string,
            'timestamp': datetime.now().isoformat(),
            'episode_count': ep_count
        })
        
        # Trigger Plex scan if enabled
        if bot_instance.config.plex.scan_on_notification and bot_instance.config.plex.enabled:
            logger.info("Triggering Plex library scan after Sonarr notification")
            # Use series path to find and scan only the relevant library
            library_name = bot_instance.config.plex.library_name if bot_instance.config.plex.library_name else None
            await scan_plex_library_async(library_name, series_path)
    except Exception as e:
        logger.critical(
            f"CRITICAL ERROR in _process_and_send_buffered_notifications: {e}", exc_info=True)

# --- Webhook Endpoints ---

# In media_watcher_service.py


@app.route('/webhook/readarr', methods=['POST'])
async def readarr_webhook():
    """Handles Readarr 'On Download' / 'On Upgrade' events."""
    logger.info("Received Readarr webhook request")
    try:
        payload = request.json
        event_type = payload.get('eventType')

        # Updated to include 'Rename' for mass updates
        if event_type not in ['Download', 'Upgrade', 'Rename', 'Test']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            return jsonify({"status": "error", "message": "Bot instance missing"}), 500

        # Dispatch the event to the new AudiobookCog
        # We run this as a task so we don't block the webhook response
        bot_instance.loop.create_task(
            bot_instance.get_cog("Audiobook").process_readarr_event(payload)
        )

        return jsonify({"status": "success", "message": "Event queued for processing"}), 200

    except Exception as e:
        logger.error(f"Error processing Readarr webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/webhook/radarr', methods=['POST'])
async def radarr_webhook_detailed():
    """Handles Radarr's 'On Grab' and 'On Download' webhook events."""
    logger.info("Received Radarr webhook request")
    try:
        payload = request.json
        if not payload:
            logger.error("No JSON payload received in Radarr webhook")
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            logger.error("Discord bot instance missing in Flask config")
            return jsonify({"status": "error", "message": "Internal server error"}), 500

        config = bot_instance.config
        tmdb_api_key = config.tmdb.api_key

        event_type = payload.get('eventType')
        logger.info(f"Radarr Event Type: {event_type}")

        if event_type not in ['Download', 'Grab', 'Test']:
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        if event_type == 'Test':
            logger.info("Processing Radarr Test Event")
            return jsonify({"status": "success", "message": "Test event received"}), 200

        movie_data = payload.get('movie', {})
        movie_file_data = payload.get('movieFile', {})
        remote_movie_data = payload.get('remoteMovie', {})
        movie_path = movie_data.get('path')  # Extract movie path for Plex scanning

        # Deduplication
        unique_key = (movie_data.get('tmdbId'), movie_file_data.get(
            'relativePath', 'unknown'), event_type)
        if unique_key in NOTIFIED_MOVIES_CACHE:
            logger.info(f"Duplicate Radarr event ignored: {unique_key}")
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
            ping_string = " ".join([f"<@{uid}>" for uid in user_ids_to_notify])
            title = movie_data.get('title', 'Unknown Movie')

            # Combine Title + Pings
            mentions = f"New Movie: **{title}** {ping_string}"

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
        
        # Record notification in history
        NOTIFICATION_HISTORY.append({
            'type': 'radarr',
            'title': title,
            'year': year,
            'quality': quality,
            'timestamp': datetime.now().isoformat()
        })
        
        # Trigger Plex scan if enabled
        if config.plex.scan_on_notification and config.plex.enabled:
            logger.info("Triggering Plex library scan after Radarr notification")
            # Use movie path to find and scan only the relevant library
            library_name = config.plex.library_name if config.plex.library_name else None
            scan_coro = scan_plex_library_async(library_name, movie_path)
            asyncio.run_coroutine_threadsafe(scan_coro, bot_instance.loop)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing Radarr webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/webhook/sonarr', methods=['POST'])
async def sonarr_webhook():
    """Handles Sonarr's 'On Grab' and 'On Download' webhook events with debouncing."""
    logger.info("Received Sonarr webhook request")
    try:
        payload = request.json
        if not payload:
            logger.error("No JSON payload received in Sonarr webhook")
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            logger.error("Discord bot instance missing in Flask config")
            return jsonify({"status": "error", "message": "Internal server error"}), 500

        config = bot_instance.config

        event_type = payload.get('eventType')
        logger.info(f"Sonarr Event Type: {event_type}")

        # ADDED 'Test' to supported events so you can see logs when testing!
        if event_type not in ['Download', 'EpisodeImport', 'Grab', 'Test']:
            logger.info(f"Ignored Sonarr event type: {event_type}")
            return jsonify({"status": "ignored", "reason": "Unsupported event type"}), 200

        series_data = payload.get('series', {})
        series_id = series_data.get('id')

        # --- Handle Test Event Immediately ---
        if event_type == 'Test':
            logger.info(
                "Processing Sonarr Test Event (Simulating notification)")
            # Create dummy episode data for the test
            dummy_episode = {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Test Episode",
                "overview": "This is a test notification from Sonarr to verify Discord integration.",
                "airDate": datetime.now().strftime("%Y-%m-%d")
            }
            # Put it in buffer directly
            EPISODE_NOTIFICATION_BUFFER[series_id].append({
                "episode_data": dummy_episode,
                "episode_unique_id": f"test_{datetime.now().timestamp()}",
                "series_data_ref": series_data,
                "quality": "Test Quality 1080p"
            })

            # Fire immediately, bypassing debounce
            coro = _process_and_send_buffered_notifications(
                series_id,
                bot_instance,
                config.discord.sonarr_notification_channel_id
            )
            asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
            return jsonify({"status": "success", "message": "Test event processed"}), 200

        if not series_id:
            logger.error("Missing series ID in Sonarr payload")
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
                logger.info(
                    f"Skipping duplicate episode notification: {unique_id}")
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
            logger.info(f"Timer fired for series_id: {series_id}")
            coro = _process_and_send_buffered_notifications(
                series_id,
                bot_instance,
                config.discord.sonarr_notification_channel_id
            )
            asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)

        # Schedule the debounce timer
        logger.info(
            f"Scheduling notification for series {series_id} in {DEBOUNCE_SECONDS} seconds")
        SERIES_NOTIFICATION_TIMERS[series_id] = bot_instance.loop.call_later(
            DEBOUNCE_SECONDS,
            schedule_notification
        )

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing Sonarr webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- Web UI API Endpoints ---

@app.route('/api/status', methods=['GET'])
def api_status():
    """Returns the current status of the system."""
    bot_instance = app.config.get('discord_bot')
    if not bot_instance:
        return jsonify({"error": "Bot instance not available"}), 500
    
    config = bot_instance.config
    
    # Get Plex status
    plex_status = {
        "connected": False,
        "name": None,
        "scan_enabled": config.plex.scan_on_notification and config.plex.enabled,
        "library_name": config.plex.library_name,
        "libraries": []
    }
    
    try:
        from plex_utils import get_plex_client
        plex = get_plex_client()
        if plex:
            plex_status["connected"] = True
            plex_status["name"] = plex.friendlyName
            # Get library list
            sections = plex.library.sections()
            plex_status["libraries"] = [{"key": s.key, "title": s.title, "type": s.type} for s in sections]
    except Exception as e:
        logger.error(f"Error getting Plex status: {e}")
    
    # Get Discord status
    discord_status = {
        "connected": bot_instance.is_ready() if bot_instance else False,
        "username": str(bot_instance.user) if bot_instance and bot_instance.user else None
    }
    
    return jsonify({
        "plex": plex_status,
        "discord": discord_status
    })


@app.route('/api/notifications', methods=['GET'])
def api_notifications():
    """Returns recent notification history."""
    # Filter notifications from last 24 hours
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=24)
    
    recent = [
        notif for notif in NOTIFICATION_HISTORY
        if datetime.fromisoformat(notif['timestamp']) > cutoff
    ]
    
    # Sort by timestamp, newest first
    recent.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return jsonify({
        "notifications": recent,
        "total": len(recent)
    })


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Returns current settings."""
    bot_instance = app.config.get('discord_bot')
    if not bot_instance:
        return jsonify({"error": "Bot instance not available"}), 500
    
    config = bot_instance.config
    
    return jsonify({
        "plex": {
            "enabled": config.plex.enabled,
            "scan_on_notification": config.plex.scan_on_notification,
            "library_name": config.plex.library_name
        },
        "debounce_seconds": DEBOUNCE_SECONDS
    })


@app.route('/api/settings', methods=['PUT'])
def api_update_settings():
    """Updates settings."""
    bot_instance = app.config.get('discord_bot')
    if not bot_instance:
        return jsonify({"error": "Bot instance not available"}), 500
    
    try:
        data = request.json
        config = bot_instance.config
        
        # Update Plex settings
        if 'plex' in data:
            plex_data = data['plex']
            if 'enabled' in plex_data:
                config.plex.enabled = bool(plex_data['enabled'])
            if 'scan_on_notification' in plex_data:
                config.plex.scan_on_notification = bool(plex_data['scan_on_notification'])
            if 'library_name' in plex_data:
                # Allow null/empty string to mean "all libraries"
                library_name = plex_data['library_name']
                config.plex.library_name = library_name if library_name else None
        
        # Note: debounce_seconds is a global constant, would need more work to make it dynamic
        # For now, we'll just return success
        
        logger.info(f"Settings updated: {data}")
        return jsonify({
            "success": True,
            "message": "Settings updated successfully",
            "settings": {
                "plex": {
                    "enabled": config.plex.enabled,
                    "scan_on_notification": config.plex.scan_on_notification,
                    "library_name": config.plex.library_name
                },
                "debounce_seconds": DEBOUNCE_SECONDS
            }
        })
    except Exception as e:
        logger.error(f"Error updating settings: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/plex/scan', methods=['POST'])
def api_plex_scan():
    """Triggers a Plex library scan."""
    try:
        data = request.json or {}
        library_name = data.get('library_name')
        
        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            return jsonify({"success": False, "message": "Bot instance not available"}), 500
        
        # Run scan in async context
        loop = bot_instance.loop
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                scan_plex_library_async(library_name),
                loop
            )
            result = future.result(timeout=10)
        else:
            result = asyncio.run(scan_plex_library_async(library_name))
        
        if result:
            lib_text = library_name if library_name else "all libraries"
            return jsonify({
                "success": True,
                "message": f"Successfully triggered Plex scan for {lib_text}"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to trigger Plex scan. Check logs for details."
            }), 500
            
    except Exception as e:
        logger.error(f"Error triggering Plex scan: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500

# --- Service Setup ---


async def start_overseerr_user_sync(bot_instance: discord.Client):
    """Periodically syncs users from Overseerr."""
    config = bot_instance.config
    while True:
        global OVERSEERR_USERS_DATA
        OVERSEERR_USERS_DATA = await fetch_overseerr_users()
        await asyncio.sleep(config.overseerr.refresh_interval_minutes * 60)


# Serve static files from webui build
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_webui(path):
    """Serves the React web UI."""
    webui_build_path = Path(__file__).parent / 'webui' / 'dist'
    
    if path and (webui_build_path / path).exists():
        return send_from_directory(str(webui_build_path), path)
    else:
        # Serve index.html for all routes (React Router)
        index_path = webui_build_path / 'index.html'
        if index_path.exists():
            return send_from_directory(str(webui_build_path), 'index.html')
        else:
            return jsonify({"error": "Web UI not built. Run 'npm run build' in the webui directory."}), 404


def run_webhook_server(bot_instance: discord.Client):
    """Starts the Flask server in a separate thread."""
    app.config['discord_bot'] = bot_instance
    # Ensure Flask logs errors to stdout
    app.logger.setLevel(logging.INFO)
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
