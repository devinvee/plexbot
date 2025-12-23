"""
Plex utility functions.
"""
import os
import logging
import requests
from typing import Optional
from urllib.parse import quote
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


def find_plex_library_for_path(media_path: str):
    """
    Finds which Plex library section contains the given media path.
    
    Args:
        media_path: The file system path to the media (e.g., /mnt/media/TV Shows/Dragon Ball Z)
    
    Returns:
        Tuple of (section object, section_id) if found, or (None, None) if not found.
    """
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot find Plex library: client not available")
        return None, None
    
    try:
        sections = plex.library.sections()
        # Normalize the media path for comparison
        media_path_normalized = os.path.normpath(media_path)
        
        for section in sections:
            # Get the locations (paths) for this library section
            locations = section.locations
            for location in locations:
                location_normalized = os.path.normpath(location)
                # Check if the media path is within this library location
                try:
                    # Use os.path.commonpath to check if media_path is within location
                    common = os.path.commonpath([media_path_normalized, location_normalized])
                    if common == location_normalized or location_normalized in media_path_normalized:
                        logger.info(f"Found matching library: {section.title} (ID: {section.key}) for path: {media_path}")
                        return section, section.key
                except ValueError:
                    # Paths are on different drives or incompatible
                    continue
        
        logger.warning(f"Could not find Plex library for path: {media_path}")
        return None, None
    except Exception as e:
        logger.error(f"Error finding Plex library for path {media_path}: {e}", exc_info=True)
        return None, None


def scan_plex_library(library_name: Optional[str] = None, media_path: Optional[str] = None) -> bool:
    """
    Scans a Plex library. If media_path is provided, performs a partial scan of just that folder.
    Otherwise, uses library_name or scans all libraries.

    Args:
        library_name: Optional name of the library to scan. Ignored if media_path is provided.
        media_path: Optional path to media file/folder. If provided, performs partial scan of that folder.

    Returns:
        True if scan was successful, False otherwise.
    """
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot scan Plex: client not available")
        return False

    try:
        # If media_path is provided, perform a partial scan of just that folder
        if media_path:
            section, section_id = find_plex_library_for_path(media_path)
            if not section or not section_id:
                logger.warning(f"Could not determine library for path {media_path}, skipping scan")
                return False
            
            # Use Plex API to perform partial scan of the specific folder
            plex_url = os.getenv("PLEX_URL")
            plex_token = os.getenv("PLEX_TOKEN")
            
            # Call Plex partial scan API
            scan_url = f"{plex_url}/library/sections/{section_id}/refresh"
            params = {
                'path': media_path,
                'X-Plex-Token': plex_token
            }
            
            response = requests.post(scan_url, params=params, timeout=10)
            response.raise_for_status()
            
            logger.info(
                f"Successfully triggered Plex partial scan for: {media_path} (library: {section.title})")
            return True
        
        if library_name:
            # Scan specific library (full scan)
            section = plex.library.section(library_name)
            section.update()
            logger.info(
                f"Successfully triggered Plex scan for library: {library_name}")
            return True
        else:
            # Scan all libraries (fallback)
            sections = plex.library.sections()
            for section in sections:
                section.update()
            logger.info(
                f"Successfully triggered Plex scan for all libraries ({len(sections)} sections)")
            return True
    except Exception as e:
        logger.error(f"Failed to scan Plex library: {e}", exc_info=True)
        return False


async def scan_plex_library_async(library_name: Optional[str] = None, media_path: Optional[str] = None) -> bool:
    """
    Async wrapper for scanning a Plex library.
    
    Args:
        library_name: Optional name of the library to scan. Ignored if media_path is provided.
        media_path: Optional path to media file/folder. If provided, finds the matching library.
    
    Returns:
        True if scan was successful, False otherwise.
    """
    import asyncio
    return await asyncio.to_thread(scan_plex_library, library_name, media_path)
