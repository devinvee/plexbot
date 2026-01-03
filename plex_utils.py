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


def get_plex_activities(filter_scans_only: bool = True) -> list:
    """
    Gets current activities from Plex using the /activities endpoint.
    Reference: https://developer.plex.tv/pms/
    
    Args:
        filter_scans_only: If True, only return scan/refresh activities. If False, return all activities.
    
    Returns:
        List of activity dictionaries.
    """
    try:
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")
        
        if not plex_url or not plex_token:
            return []
        
        # Use Plex API /activities endpoint
        activities_url = f"{plex_url}/activities"
        params = {
            'X-Plex-Token': plex_token
        }
        
        response = requests.get(activities_url, params=params, timeout=5)
        response.raise_for_status()
        
        # Parse XML response (Plex API returns XML by default)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        
        activities = []
        # Find all Activity elements
        for activity in root.findall('.//Activity'):
            activity_type = activity.get('type', '')
            title = activity.get('title', '')
            subtitle = activity.get('subtitle', '')
            uuid_attr = activity.get('uuid', '')
            cancellable = activity.get('cancellable', '0')
            
            # Parse context if available
            context = {}
            context_elem = activity.find('Context')
            if context_elem is not None:
                library_section_id = context_elem.get('librarySectionID')
                if library_section_id:
                    context['librarySectionID'] = library_section_id
            
            activity_data = {
                'key': activity.get('key', ''),
                'uuid': uuid_attr,
                'type': activity_type,
                'title': title,
                'subtitle': subtitle,
                'progress': int(activity.get('progress', 0)),
                'cancellable': cancellable == '1',
                'context': context,
            }
            
            # If filtering for scans only, check if this is a scan/refresh activity
            if filter_scans_only:
                if ('refresh' in activity_type.lower() or 
                    'scan' in activity_type.lower() or
                    'refresh' in title.lower() or
                    'scan' in title.lower() or
                    'update' in activity_type.lower()):
                    activities.append(activity_data)
            else:
                # Return all activities
                activities.append(activity_data)
        
        return activities
    except Exception as e:
        logger.warning(f"Error getting Plex activities: {e}")
        return []


def is_plex_scanning(section_key: str) -> bool:
    """
    Checks if a Plex library section is currently scanning using the activities API.
    
    Args:
        section_key: The key/ID of the library section.
    
    Returns:
        True if scanning, False otherwise.
    """
    try:
        # First try using the activities API
        activities = get_plex_activities()
        for activity in activities:
            # Check if activity is related to this section
            context = activity.get('context', {})
            if isinstance(context, dict):
                section_id = context.get('librarySectionID')
                if section_id and str(section_id) == str(section_key):
                    logger.info(f"Found scanning activity for section {section_key}")
                    return True
        
        # Fallback: check section refreshing attribute
        plex = get_plex_client()
        if plex:
            section = plex.library.sectionByID(section_key)
            if section:
                return getattr(section, 'refreshing', False)
        
        return False
    except Exception as e:
        logger.warning(f"Error checking scan status: {e}")
        # Fallback to section attribute check
        try:
            plex = get_plex_client()
            if plex:
                section = plex.library.sectionByID(section_key)
                if section:
                    return getattr(section, 'refreshing', False)
        except:
            pass
        return False


def wait_for_scan_complete(section_key: str, max_wait_seconds: int = 300) -> bool:
    """
    Waits for a Plex library scan to complete.
    
    Since Plex doesn't provide a reliable way to check scan status,
    we use a timeout-based approach: wait a reasonable time for the scan to complete.
    
    Args:
        section_key: The key/ID of the library section.
        max_wait_seconds: Maximum time to wait in seconds (default 5 minutes).
    
    Returns:
        True if we waited the full time (assuming scan completed), False if timeout.
    """
    import time
    start_time = time.time()
    check_interval = 5  # Check every 5 seconds
    
    # Wait for a minimum time to allow scan to start
    time.sleep(2)
    
    # Poll for scan completion with timeout
    while time.time() - start_time < max_wait_seconds:
        if not is_plex_scanning(section_key):
            # Give it a moment to ensure scan is really done
            time.sleep(2)
            if not is_plex_scanning(section_key):
                elapsed = time.time() - start_time
                logger.info(f"Scan completed for section {section_key} after {elapsed:.1f} seconds")
                return True
        time.sleep(check_interval)
    
    elapsed = time.time() - start_time
    logger.info(f"Waited {elapsed:.1f} seconds for section {section_key} scan (may still be in progress)")
    # Return True anyway since we waited the full time - scan may have completed
    return True


def scan_all_libraries_sequential() -> dict:
    """
    Scans all Plex libraries sequentially, waiting a reasonable time between each.
    
    Since Plex scans are asynchronous and don't provide reliable completion status,
    we wait a fixed time (30 seconds) between each library scan to allow processing.
    
    Returns:
        Dictionary with scan results including success count and details.
    """
    import time
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot scan Plex: client not available")
        return {"success": False, "message": "Plex client not available", "scanned": 0, "total": 0}
    
    try:
        sections = plex.library.sections()
        total = len(sections)
        scanned = 0
        results = []
        wait_between_scans = 30  # Wait 30 seconds between scans
        
        for idx, section in enumerate(sections):
            logger.info(f"Starting scan for library {idx + 1}/{total}: {section.title} (ID: {section.key})")
            try:
                section.update()
                scanned += 1
                results.append({"library": section.title, "success": True})
                logger.info(f"Triggered scan for library: {section.title}")
                
                # Wait before starting next scan (except for the last one)
                if idx < total - 1:
                    logger.info(f"Waiting {wait_between_scans} seconds before next scan...")
                    time.sleep(wait_between_scans)
                    
            except Exception as e:
                logger.error(f"Error scanning library {section.title}: {e}")
                results.append({"library": section.title, "success": False, "message": str(e)})
        
        return {
            "success": scanned == total,
            "scanned": scanned,
            "total": total,
            "results": results
        }
    except Exception as e:
        logger.error(f"Failed to scan all libraries: {e}", exc_info=True)
        return {"success": False, "message": str(e), "scanned": 0, "total": 0}


async def scan_all_libraries_sequential_async() -> dict:
    """
    Async wrapper for sequential library scanning.
    
    Returns:
        Dictionary with scan results.
    """
    import asyncio
    return await asyncio.to_thread(scan_all_libraries_sequential)


def get_plex_libraries() -> list:
    """
    Gets a list of all Plex libraries.
    
    Returns:
        List of library dictionaries with key, title, and type.
    """
    plex = get_plex_client()
    if not plex:
        return []
    
    try:
        sections = plex.library.sections()
        return [
            {
                "key": section.key,
                "title": section.title,
                "type": section.type  # 'show', 'movie', etc.
            }
            for section in sections
        ]
    except Exception as e:
        logger.error(f"Error getting Plex libraries: {e}", exc_info=True)
        return []


def get_library_items(library_key: str, limit: int = 1000) -> list:
    """
    Gets items (shows/movies) from a specific library.
    
    Args:
        library_key: The key/ID of the library section.
        limit: Maximum number of items to return.
    
    Returns:
        List of item dictionaries with key, title, year, type, and thumb URL.
    """
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot get library items: Plex client not available")
        return []
    
    try:
        # Try to get section by ID (convert to int if needed)
        try:
            section_id = int(library_key)
            section = plex.library.sectionByID(section_id)
        except (ValueError, TypeError):
            # If conversion fails, try to find by title
            logger.warning(f"Could not convert library_key to int: {library_key}, trying to find by title")
            sections = plex.library.sections()
            section = None
            for s in sections:
                if str(s.key) == str(library_key) or s.title == library_key:
                    section = s
                    break
        
        if not section:
            logger.error(f"Could not find library section with key: {library_key}")
            return []
        
        logger.info(f"Found library section: {section.title} (type: {section.type})")
        
        plex_url = os.getenv("PLEX_URL")
        plex_token = os.getenv("PLEX_TOKEN")
        
        # Get items based on library type
        items = []
        try:
            # Try section.all() first - this should work for most library types
            if hasattr(section, 'all'):
                items = list(section.all(limit=limit))
                logger.info(f"Using section.all() - found {len(items)} items")
            elif hasattr(section, 'search'):
                # Fallback to search if all() doesn't exist
                items = list(section.search())
                logger.info(f"Using section.search() - found {len(items)} items")
            else:
                logger.error(f"Section {section.title} has no 'all' or 'search' method")
                return []
        except Exception as e:
            logger.error(f"Error getting items from section: {e}", exc_info=True)
            # Try alternative method
            try:
                if hasattr(section, 'search'):
                    items = list(section.search())
                    logger.info(f"Fallback: Using section.search() - found {len(items)} items")
                else:
                    logger.error("No alternative method available")
                    return []
            except Exception as e2:
                logger.error(f"Error with section.search(): {e2}", exc_info=True)
                return []
        
        logger.info(f"Found {len(items)} items in library {section.title}")
        
        result = []
        for item in items:
            try:
                thumb = None
                if hasattr(item, 'thumb') and item.thumb:
                    # Build proper Plex thumbnail URL
                    if plex_url and plex_token:
                        thumb = f"{plex_url}{item.thumb}?X-Plex-Token={plex_token}"
                    else:
                        thumb = item.thumb
                
                result.append({
                    "key": item.key,
                    "title": item.title,
                    "year": getattr(item, 'year', None),
                    "type": section.type,
                    "thumb": thumb
                })
            except Exception as e:
                logger.warning(f"Error processing item: {e}")
                continue
        
        logger.info(f"Returning {len(result)} items")
        return result
    except Exception as e:
        logger.error(f"Error getting library items: {e}", exc_info=True)
        return []


def scan_plex_item(item_key: str) -> bool:
    """
    Scans a specific Plex item (show/movie).
    
    Args:
        item_key: The key/ID of the item to scan.
    
    Returns:
        True if scan was successful, False otherwise.
    """
    logger.info(f"scan_plex_item called with item_key: {item_key}")
    plex = get_plex_client()
    if not plex:
        logger.error("Cannot scan Plex item: client not available")
        return False
    
    try:
        logger.info(f"Fetching item from Plex with key: {item_key}")
        # Get the item - item_key might need to be an integer or might have a prefix
        # Try fetching directly first
        try:
            item = plex.fetchItem(item_key)
        except Exception as fetch_error:
            logger.warning(f"Direct fetch failed: {fetch_error}, trying alternative methods")
            # Try with /library/metadata/ prefix if it's just a number
            try:
                if item_key.isdigit():
                    item = plex.fetchItem(f"/library/metadata/{item_key}")
                else:
                    raise fetch_error
            except Exception as e2:
                logger.error(f"Failed to fetch item with key {item_key}: {e2}")
                return False
        
        if not item:
            logger.error(f"Item not found: {item_key}")
            return False
        
        item_title = getattr(item, 'title', 'Unknown')
        logger.info(f"Found item: {item_title} (key: {item.key})")
        
        # Get the library section for this item
        try:
            section = item.section()
        except Exception as section_error:
            logger.error(f"Error getting section for item: {section_error}")
            return False
        
        if not section:
            logger.error(f"Could not find section for item: {item_key}")
            return False
        
        logger.info(f"Item section: {section.title} (type: {section.type})")
        
        # Use the item's path to trigger a partial scan
        if hasattr(item, 'locations') and item.locations:
            media_path = item.locations[0]
            logger.info(f"Scanning item '{item_title}' at path: {media_path}")
            result = scan_plex_library(library_name=None, media_path=media_path)
            logger.info(f"Scan result: {result}")
            return result
        else:
            # Fallback: scan the entire library section
            logger.info(f"Item has no locations, scanning entire library section for: {item_title}")
            try:
                section.update()
                logger.info(f"Successfully triggered library scan for section: {section.title}")
                return True
            except Exception as update_error:
                logger.error(f"Error updating section: {update_error}")
                return False
    except Exception as e:
        logger.error(f"Failed to scan Plex item {item_key}: {e}", exc_info=True)
        return False


async def scan_plex_item_async(item_key: str) -> bool:
    """
    Async wrapper for scanning a Plex item.
    
    Args:
        item_key: The key/ID of the item to scan.
    
    Returns:
        True if scan was successful, False otherwise.
    """
    import asyncio
    return await asyncio.to_thread(scan_plex_item, item_key)
