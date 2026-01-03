"""
Media watcher service for the Plex Discord Bot.
"""
import asyncio
import logging
import uuid
from collections import deque, defaultdict
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from typing import Dict, Any, Set, Deque
import discord

from config import BotConfig, bot_config
from media_watcher_utils import (
    fetch_tmdb_movie_details,
    get_discord_user_ids_for_tags,
    fetch_overseerr_users,
)
from plex_utils import (
    scan_plex_library_async,
    scan_all_libraries_sequential_async,
    get_plex_libraries,
    get_library_items,
    scan_plex_item_async
)

logger = logging.getLogger(__name__)

# --- State Management ---
EPISODE_NOTIFICATION_BUFFER: Dict[str, list] = defaultdict(list)
SERIES_NOTIFICATION_TIMERS: Dict[str, asyncio.TimerHandle] = {}
NOTIFIED_EPISODES_CACHE: Deque[tuple] = deque(maxlen=1000)
NOTIFIED_MOVIES_CACHE: Deque[tuple] = deque(maxlen=1000)
OVERSEERR_USERS_DATA: Dict[str, dict] = {}
NOTIFICATION_HISTORY: Deque[Dict[str, Any]] = deque(
    maxlen=100)  # Store last 100 notifications
PENDING_SCANS: Dict[str, Dict[str, Any]] = {}  # Track pending scans by scan_id

DEBOUNCE_SECONDS = 60

app = Flask(__name__)


def add_pending_scan(scan_type: str, scan_id: str, name: str, library_key: str = None, item_key: str = None):
    """Add a scan to the pending scans list."""
    PENDING_SCANS[scan_id] = {
        "scan_id": scan_id,
        "type": scan_type,  # 'library', 'item', 'all_libraries'
        "name": name,
        "library_key": library_key,
        "item_key": item_key,
        "status": "pending",
        "timestamp": datetime.now().isoformat(),
        "checked_at": datetime.now().isoformat()
    }
    logger.info(f"Added pending scan: {scan_id} ({scan_type}) - {name}")


def check_scan_status(scan_id: str) -> str:
    """Check if a scan is still pending or completed using Plex Activities API."""
    if scan_id not in PENDING_SCANS:
        return "unknown"

    scan_info = PENDING_SCANS[scan_id]
    scan_type = scan_info.get("type")
    library_key = scan_info.get("library_key")

    # Get current activities from Plex
    from plex_utils import get_plex_activities, is_plex_scanning
    activities = get_plex_activities()
    
    # Check if there are any scanning activities
    has_scanning_activity = len(activities) > 0

    if scan_type == "library" and library_key:
        # Check if this specific library is scanning
        try:
            section_id = int(library_key)
            # Check activities for this specific section
            section_scanning = False
            for activity in activities:
                # Try to extract section ID from activity context
                # Activities might have librarySectionID in context
                context = activity.get('context', {})
                if isinstance(context, dict):
                    act_section_id = context.get('librarySectionID') or context.get('sectionID')
                    if act_section_id and str(act_section_id) == str(section_id):
                        section_scanning = True
                        logger.info(f"Found scanning activity for section {section_id}")
                        break
            
            # Also check using the section's refreshing attribute
            if not section_scanning:
                section_scanning = is_plex_scanning(section_id)
            
            if not section_scanning:
                # No scanning activity found, check timeout
                scan_timestamp = datetime.fromisoformat(scan_info["timestamp"])
                time_since_scan = (datetime.now() - scan_timestamp).total_seconds()
                # If no activity and it's been more than 2 minutes, assume completed
                if time_since_scan > 120:
                    scan_info["status"] = "completed"
                    scan_info["completed_at"] = datetime.now().isoformat()
                    return "completed"
            return "pending"
        except Exception as e:
            logger.warning(f"Error checking scan status for {scan_id}: {e}")
            # Fallback to timeout
            scan_timestamp = datetime.fromisoformat(scan_info["timestamp"])
            time_since_scan = (datetime.now() - scan_timestamp).total_seconds()
            if time_since_scan > 300:  # 5 minutes
                scan_info["status"] = "completed"
                scan_info["completed_at"] = datetime.now().isoformat()
                return "completed"
            return "pending"
    elif scan_type == "all_libraries":
        # For all libraries scan, check if any scanning activities exist
        if not has_scanning_activity:
            # No scanning activities, check timeout
            scan_timestamp = datetime.fromisoformat(scan_info["timestamp"])
            time_since_scan = (datetime.now() - scan_timestamp).total_seconds()
            # If no activity and it's been more than 5 minutes, assume completed
            if time_since_scan > 300:
                scan_info["status"] = "completed"
                scan_info["completed_at"] = datetime.now().isoformat()
                return "completed"
        return "pending"
    else:
        # For item scans, check the library status
        if library_key:
            try:
                section_id = int(library_key)
                is_scanning = is_plex_scanning(section_id)
                if not is_scanning:
                    scan_timestamp = datetime.fromisoformat(scan_info["timestamp"])
                    time_since_scan = (datetime.now() - scan_timestamp).total_seconds()
                    if time_since_scan > 120:  # 2 minutes
                        scan_info["status"] = "completed"
                        scan_info["completed_at"] = datetime.now().isoformat()
                        return "completed"
                return "pending"
            except:
                pass
        # Default: assume completed after 5 minutes
        scan_timestamp = datetime.fromisoformat(scan_info["timestamp"])
        time_since_scan = (datetime.now() - scan_timestamp).total_seconds()
        if time_since_scan > 300:
            scan_info["status"] = "completed"
            scan_info["completed_at"] = datetime.now().isoformat()
            return "completed"
        return "pending"

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

        # Sort episodes by season and episode number
        sorted_items = sorted(
            buffered_items,
            key=lambda x: (
                x.get('episode_data', {}).get('seasonNumber', 0),
                x.get('episode_data', {}).get('episodeNumber', 0)
            )
        )

        # Use the latest item for series metadata
        latest_item = sorted_items[-1]
        series_data = latest_item.get('series_data_ref', {})
        episode_data = latest_item.get('episode_data', {})
        quality_string = latest_item.get('quality', 'N/A')
        # Extract series path for Plex scanning
        series_path = series_data.get('path')

        # --- Build Rich Embed (Sonarr) ---
        embed = discord.Embed(color=0x00A4DC)  # Sonarr Blue
        embed.set_author(name="New Episode Available - Sonarr",
                         icon_url="https://i.imgur.com/tV61XQZ.png")

        # Build episode list
        episode_list = []
        for item in sorted_items:
            ep_data = item.get('episode_data', {})
            season_num = ep_data.get('seasonNumber', 0)
            episode_num = ep_data.get('episodeNumber', 0)
            ep_title = ep_data.get('title', 'Unknown Title')
            ep_string = f"S{season_num:02d}E{episode_num:02d}"
            episode_list.append(f"{ep_string} - {ep_title}")

        # Title - show range if multiple episodes, single if one
        ep_count = len(sorted_items)
        if ep_count == 1:
            season_num = episode_data.get('seasonNumber', 0)
            episode_num = episode_data.get('episodeNumber', 0)
            ep_string = f"S{season_num:02d}E{episode_num:02d}"
            embed.title = f"{series_data.get('title', 'Unknown Series')} ({series_data.get('year', 'N/A')}) ({ep_string})"
        else:
            first_ep = sorted_items[0].get('episode_data', {})
            last_ep = sorted_items[-1].get('episode_data', {})
            first_season = first_ep.get('seasonNumber', 0)
            first_ep_num = first_ep.get('episodeNumber', 0)
            last_season = last_ep.get('seasonNumber', 0)
            last_ep_num = last_ep.get('episodeNumber', 0)
            first_string = f"S{first_season:02d}E{first_ep_num:02d}"
            last_string = f"S{last_season:02d}E{last_ep_num:02d}"
            embed.title = f"{series_data.get('title', 'Unknown Series')} ({series_data.get('year', 'N/A')}) ({first_string} - {last_string})"

        # List all episodes
        episodes_text = "\n".join(episode_list)
        if len(episodes_text) > 1024:
            # Discord field limit is 1024 characters, truncate if needed
            episodes_text = episodes_text[:1021] + "..."
        embed.add_field(
            name=f"Imported Episodes ({ep_count})",
            value=episodes_text,
            inline=False
        )

        # Overview (from latest episode)
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
            if ep_count == 1:
                season_num = episode_data.get('seasonNumber', 0)
                episode_num = episode_data.get('episodeNumber', 0)
                ep_string = f"S{season_num:02d}E{episode_num:02d}"
                mentions_text = f"Episode **{ep_string}** of **{title}** is now available! {ping_string}"
            else:
                mentions_text = f"**{ep_count} episodes** of **{title}** are now available! {ping_string}"
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
        # Build list of all episodes for history
        all_episodes = []
        for item in sorted_items:
            ep_data = item.get('episode_data', {})
            all_episodes.append({
                'season': ep_data.get('seasonNumber', 0),
                'number': ep_data.get('episodeNumber', 0),
                'title': ep_data.get('title', 'Unknown Title'),
                'airDate': ep_data.get('airDate', 'N/A'),
                'overview': ep_data.get('overview', '')
            })

        latest_season_num = episode_data.get('seasonNumber', 0)
        latest_episode_num = episode_data.get('episodeNumber', 0)
        latest_episode_title = episode_data.get('title', 'Unknown Title')
        NOTIFICATION_HISTORY.append({
            'type': 'sonarr',
            'title': series_data.get('title', 'Unknown Series'),
            'year': series_data.get('year'),
            'episode': {
                'season': latest_season_num,
                'number': latest_episode_num,
                'title': latest_episode_title
            },
            'episodes': all_episodes,  # All episodes in this batch
            'quality': quality_string,
            'timestamp': datetime.now().isoformat(),
            'episode_count': ep_count,
            'poster_url': poster_url if poster_url and poster_url.startswith("http") else None,
            'fanart_url': fanart_url if fanart_url and fanart_url.startswith("http") else None
        })

        # Trigger Plex scan if enabled
        if bot_instance.config.plex.scan_on_notification and bot_instance.config.plex.enabled:
            logger.info(
                "Triggering Plex library scan after Sonarr notification")
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
        # Extract movie path for Plex scanning
        movie_path = movie_data.get('path')

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
        # Build image URLs from TMDB data
        poster_url = None
        backdrop_url = None
        if tmdb_details:
            poster_path = tmdb_details.get('poster_path')
            backdrop_path = tmdb_details.get('backdrop_path')
            if poster_path:
                poster_url = f"https://image.tmdb.org/t/p/w300{poster_path}"
            if backdrop_path:
                backdrop_url = f"https://image.tmdb.org/t/p/w1280{backdrop_path}"

        NOTIFICATION_HISTORY.append({
            'type': 'radarr',
            'title': title,
            'year': year,
            'quality': quality,
            'timestamp': datetime.now().isoformat(),
            'poster_url': poster_url,
            'backdrop_url': backdrop_url
        })

        # Trigger Plex scan if enabled
        if config.plex.scan_on_notification and config.plex.enabled:
            logger.info(
                "Triggering Plex library scan after Radarr notification")
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
            plex_status["libraries"] = [
                {"key": s.key, "title": s.title, "type": s.type} for s in sections]
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


@app.route('/api/config', methods=['GET'])
def api_get_config():
    """Returns the full configuration with processed values."""
    try:
        import json
        from utils import _replace_placeholders
        CONFIG_FILE = "config.json"

        # Load raw config (with placeholders)
        with open(CONFIG_FILE, 'r') as f:
            raw_config = json.load(f)

        # Process placeholders to show actual values
        processed_config = _replace_placeholders(raw_config)

        return jsonify({
            "success": True,
            "config": processed_config
        })
    except Exception as e:
        logger.error(f"Error loading config: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/config', methods=['PUT'])
def api_update_config():
    """Updates the full configuration and reloads it."""
    bot_instance = app.config.get('discord_bot')
    if not bot_instance:
        return jsonify({"error": "Bot instance not available"}), 500

    try:
        import json
        from utils import load_config
        CONFIG_FILE = "config.json"

        data = request.json
        if 'config' not in data:
            return jsonify({"success": False, "error": "Missing 'config' in request"}), 400

        new_config = data['config']

        # Save to file
        with open(CONFIG_FILE, 'w') as f:
            json.dump(new_config, f, indent=2)

        logger.info("Config file updated, reloading configuration...")

        # Reload config (this updates the in-memory config)
        processed_config = load_config(CONFIG_FILE)

        # Update bot instance config reference (bot_config is updated by load_config)
        bot_instance.config = bot_config

        return jsonify({
            "success": True,
            "message": "Configuration updated and reloaded successfully"
        })
    except Exception as e:
        logger.error(f"Error updating config: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Returns current settings (legacy endpoint for backward compatibility)."""
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
                config.plex.scan_on_notification = bool(
                    plex_data['scan_on_notification'])
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


@app.route('/api/plex/scan-all', methods=['POST'])
def api_plex_scan_all():
    """Sequentially scans all Plex libraries, waiting for each to complete."""
    try:
        scan_id = f"all_libraries_{uuid.uuid4().hex[:8]}"
        add_pending_scan("all_libraries", scan_id, "All Libraries")

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            return jsonify({"success": False, "message": "Bot instance not available"}), 500

        # Run scan in async context
        loop = bot_instance.loop
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                scan_all_libraries_sequential_async(),
                loop
            )
            result = future.result(timeout=600)  # 10 minute timeout
        else:
            result = asyncio.run(scan_all_libraries_sequential_async())

        result["scan_id"] = scan_id
        return jsonify(result)

    except Exception as e:
        logger.error(
            f"Error triggering sequential Plex scan: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500


@app.route('/api/plex/libraries', methods=['GET'])
def api_plex_libraries():
    """Gets a list of all Plex libraries."""
    try:
        libraries = get_plex_libraries()
        return jsonify({
            "success": True,
            "libraries": libraries
        })
    except Exception as e:
        logger.error(f"Error getting Plex libraries: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500


@app.route('/api/plex/library/<library_key>/items', methods=['GET'])
def api_plex_library_items(library_key):
    """Gets items (shows/movies) from a specific library."""
    try:
        limit = request.args.get('limit', 1000, type=int)
        logger.info(
            f"Fetching items for library key: {library_key}, limit: {limit}")
        items = get_library_items(library_key, limit=limit)
        logger.info(
            f"Returning {len(items)} items for library key: {library_key}")
        return jsonify({
            "success": True,
            "items": items,
            "count": len(items)
        })
    except Exception as e:
        logger.error(f"Error getting library items: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}",
            "items": []
        }), 500


@app.route('/api/plex/item/scan', methods=['POST'])
def api_plex_item_scan():
    """Scans a specific Plex item (show/movie)."""
    try:
        data = request.json or {}
        item_key = data.get('item_key')
        item_name = data.get('item_name', 'Unknown Item')

        if not item_key:
            return jsonify({
                "success": False,
                "message": "Missing item_key in request body"
            }), 400

        logger.info(f"Received scan request for item key: {item_key}")

        bot_instance = app.config.get('discord_bot')
        if not bot_instance:
            logger.error("Bot instance not available for item scan")
            return jsonify({"success": False, "message": "Bot instance not available"}), 500

        # Get library key from item if possible
        library_key = None
        try:
            from plex_utils import get_plex_client
            plex = get_plex_client()
            if plex:
                item = plex.fetchItem(item_key)
                if item:
                    section = item.section()
                    if section:
                        library_key = str(section.key)
        except Exception as e:
            logger.warning(f"Could not determine library for item: {e}")

        # Create scan ID and add to pending scans
        scan_id = f"item_{uuid.uuid4().hex[:8]}"
        add_pending_scan("item", scan_id, item_name,
                         library_key=library_key, item_key=item_key)

        logger.info(f"Starting async scan for item: {item_key}")
        # Run scan in async context
        loop = bot_instance.loop
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                scan_plex_item_async(item_key),
                loop
            )
            try:
                result = future.result(timeout=30)
            except asyncio.TimeoutError:
                logger.error(
                    f"Timeout waiting for item scan to complete: {item_key}")
                return jsonify({
                    "success": False,
                    "message": "Scan operation timed out"
                }), 500
        else:
            result = asyncio.run(scan_plex_item_async(item_key))

        logger.info(f"Scan result for item {item_key}: {result}")
        if result:
            return jsonify({
                "success": True,
                "message": "Successfully triggered Plex scan for item",
                "scan_id": scan_id
            })
        else:
            logger.warning(f"Scan returned False for item: {item_key}")
            # Remove from pending if it failed
            if scan_id in PENDING_SCANS:
                PENDING_SCANS[scan_id]["status"] = "failed"
            return jsonify({
                "success": False,
                "message": "Failed to trigger Plex scan for item. Check server logs for details."
            }), 500

    except Exception as e:
        logger.error(
            f"Error triggering item scan: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500


@app.route('/api/plex/pending-scans', methods=['GET'])
def api_pending_scans():
    """Gets the list of pending scans."""
    try:
        # Update status for all pending scans
        for scan_id in list(PENDING_SCANS.keys()):
            scan_info = PENDING_SCANS[scan_id]
            if scan_info.get("status") == "pending":
                check_scan_status(scan_id)  # Updates status internally
                scan_info["checked_at"] = datetime.now().isoformat()

        # Filter out completed scans older than 1 hour
        now = datetime.now()
        active_scans = []
        for scan_id, scan_info in PENDING_SCANS.items():
            if scan_info.get("status") == "completed":
                completed_at = scan_info.get("completed_at")
                if completed_at:
                    completed_time = datetime.fromisoformat(completed_at)
                    if (now - completed_time).total_seconds() > 3600:  # 1 hour
                        continue
            active_scans.append(scan_info)

        # Sort by timestamp, newest first
        active_scans.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return jsonify({
            "success": True,
            "pending_scans": active_scans
        })
    except Exception as e:
        logger.error(f"Error getting pending scans: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}",
            "pending_scans": []
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
