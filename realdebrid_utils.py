"""
Real-Debrid utility functions.
"""
import os
import logging
import aiohttp
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

API_KEY = os.environ.get("REALDEBRID_API_KEY")

async def get_realdebrid_client() -> Optional[Dict[str, Any]]:
    """
    Gets the Real-Debrid user data.

    Returns:
        The Real-Debrid user data if available, otherwise None.
    """
    if not API_KEY:
        logger.error("Real-Debrid: REALDEBRID_API_KEY is not set.")
        return None

    url = "https://api.real-debrid.com/rest/1.0/user"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    logger.debug(f"RealDebrid: Fetching user info from {url}.")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"RealDebrid: Network or API error: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.critical(f"RealDebrid: An unexpected critical error occurred: {e}", exc_info=True)
        return None
