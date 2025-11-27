"""
Utility functions for the media watcher service.
"""
import logging
import asyncio
import requests
from typing import Dict, Any, Optional, Set
from config import bot_config

logger = logging.getLogger(__name__)

async def fetch_tmdb_movie_details(tmdb_id: int, api_key: str) -> Dict[str, Any]:
    """
    Fetches detailed movie information from the TMDB API, including videos and release dates.
    """
    if not api_key:
        logger.warning("TMDB API key is not configured. Skipping TMDB fetch.")
        return {}

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

def normalize_plex_username(username: str) -> str:
    """Converts a plex username to a consistent format for tag matching."""
    return username.lower().replace(" ", "")

async def fetch_overseerr_users() -> Dict[str, Dict[str, Any]]:
    """Fetches users from Overseerr API and returns a dictionary of users."""
    overseerr_config = bot_config.get("overseerr", {})
    if not overseerr_config.get("base_url") or not overseerr_config.get("api_key"):
        logger.warning("Overseerr API config missing. Skipping user sync.")
        return {}

    url = f"{overseerr_config['base_url'].rstrip('/')}/api/v1/user?take=999"
    logger.info(f"Attempting to fetch Overseerr users from: {url}")

    headers = {
        "X-Api-Key": overseerr_config['api_key'], "Accept": "application/json"}

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        response.raise_for_status()
        parsed_data = response.json()
        users_list = parsed_data.get('results')

        if not isinstance(users_list, list):
            logger.error(
                f"Overseerr API 'results' key did not contain a list. Got: {type(users_list)}. Cannot process users.")
            return {}

        overseerr_users_data = {}
        user_mappings = bot_config.get("user_mappings", {}).get("plex_to_discord", {})
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
            discord_id = user_mappings.get(normalized_px_username)

            if plex_username and discord_id:
                overseerr_users_data[normalized_px_username] = {
                    "discord_id": discord_id,
                    "original_plex_username": plex_username
                }
        logger.info(
            f"Successfully synced {len(overseerr_users_data)} Overseerr users.")
        return overseerr_users_data

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
    return {}

def get_discord_user_ids_for_tags(media_tags: list) -> Set[str]:
    """
    Returns a set of Discord user IDs to notify based on matching Sonarr tags.
    Matches if a user's username from the config is a substring of a normalized media tag.
    """
    users_to_notify = set()
    if not media_tags:
        return users_to_notify

    normalized_media_tags = [tag.lower() for tag in media_tags]
    user_map = bot_config.get("user_mappings", {}).get("plex_to_discord", {})

    for username, discord_id in user_map.items():
        normalized_username = username.lower()
        for media_tag in normalized_media_tags:
            if normalized_username in media_tag:
                users_to_notify.add(discord_id)
                break

    logger.debug(f"Users to notify for tags {media_tags}: {users_to_notify}")
    return users_to_notify
