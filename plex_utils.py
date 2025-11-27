"""
Plex utility functions.
"""
import os
import logging
from typing import Optional
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount

logger = logging.getLogger(__name__)

def get_plex_client() -> Optional[PlexServer]:
    """
    Gets the Plex client.

    Returns:
        The Plex client if available, otherwise None.
    """
    plex_url = os.getenv("PLEX_URL")
    plex_token = os.getenv("PLEX_TOKEN")

    if not plex_url or not plex_token:
        logger.error("Plex server is not configured. Please contact the admin.")
        return None

    try:
        account = MyPlexAccount(token=plex_token)
        plex = PlexServer(plex_url, account.authenticationToken)
        return plex
    except Exception as e:
        logger.error(f"Failed to connect to Plex server: {e}")
        return None
