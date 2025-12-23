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
        logger.error(
            "Plex server is not configured. Please contact the admin.")
        return None

    try:
        account = MyPlexAccount(token=plex_token)
        plex = PlexServer(plex_url, account.authenticationToken)
        return plex
    except Exception as e:
        logger.error(f"Failed to connect to Plex server: {e}")
        return None


def scan_plex_library(library_name: Optional[str] = None) -> bool:
    """
    Scans a Plex library. If library_name is None, scans all libraries.

    Args:
        library_name: Optional name of the library to scan. If None, scans all libraries.

    Returns:
        True if scan was successful, False otherwise.
    """
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot scan Plex: client not available")
        return False

    try:
        if library_name:
            # Scan specific library
            section = plex.library.section(library_name)
            section.update()
            logger.info(
                f"Successfully triggered Plex scan for library: {library_name}")
            return True
        else:
            # Scan all libraries
            sections = plex.library.sections()
            for section in sections:
                section.update()
            logger.info(
                f"Successfully triggered Plex scan for all libraries ({len(sections)} sections)")
            return True
    except Exception as e:
        logger.error(f"Failed to scan Plex library: {e}", exc_info=True)
        return False


async def scan_plex_library_async(library_name: Optional[str] = None) -> bool:
    """
    Async wrapper for scanning a Plex library.

    Args:
        library_name: Optional name of the library to scan. If None, scans all libraries.

    Returns:
        True if scan was successful, False otherwise.
    """
    import asyncio
    return await asyncio.to_thread(scan_plex_library, library_name)
