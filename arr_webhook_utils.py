"""
Utilities for managing Sonarr/Radarr/Readarr webhooks.
"""
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def get_webhook_url(arr_type: str, bot_url: str) -> str:
    """
    Get the webhook URL for a specific ARR type.
    
    Args:
        arr_type: 'sonarr', 'radarr', or 'readarr'
        bot_url: Base URL of the bot (e.g., 'http://bot:5000')
    
    Returns:
        Full webhook URL
    """
    return f"{bot_url.rstrip('/')}/webhook/{arr_type.lower()}"


def get_existing_webhook(arr_url: str, arr_api_key: str, webhook_url: str, arr_type: str = "sonarr") -> Optional[Dict[str, Any]]:
    """
    Check if a webhook already exists for the given URL.
    
    Args:
        arr_url: Base URL of the ARR instance
        arr_api_key: API key for the ARR instance
        webhook_url: The webhook URL to check for
        arr_type: 'sonarr', 'radarr', or 'readarr'
    
    Returns:
        Existing webhook notification dict if found, None otherwise
    """
    try:
        api_url = f"{arr_url.rstrip('/')}/api/v3/notification"
        headers = {'X-Api-Key': arr_api_key}
        
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        notifications = response.json()
        
        # Find webhook notification with matching URL
        for notification in notifications:
            if notification.get('implementation') == 'Webhook' and notification.get('fields'):
                # Check if any field matches our webhook URL
                for field in notification.get('fields', []):
                    if field.get('name') == 'url' and field.get('value') == webhook_url:
                        return notification
        
        return None
    except Exception as e:
        logger.warning(f"Error checking existing webhook for {arr_type}: {e}")
        return None


def create_webhook(arr_url: str, arr_api_key: str, webhook_url: str, arr_type: str = "sonarr", name: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a webhook notification in Sonarr/Radarr/Readarr.
    
    Args:
        arr_url: Base URL of the ARR instance
        arr_api_key: API key for the ARR instance
        webhook_url: The webhook URL to create
        arr_type: 'sonarr', 'radarr', or 'readarr'
        name: Optional name for the webhook (defaults to 'PlexBot Webhook')
    
    Returns:
        Dict with 'success' bool and 'message' string
    """
    try:
        if not name:
            name = f"PlexBot {arr_type.capitalize()} Webhook"
        
        # Determine which events to listen to based on ARR type
        if arr_type.lower() == 'sonarr':
            on_grab = True
            on_download = True
            on_upgrade = True
            on_rename = False
            on_series_delete = False
            on_episode_file_delete = False
            on_health_issue = False
        elif arr_type.lower() == 'radarr':
            on_grab = True
            on_download = True
            on_upgrade = True
            on_rename = False
            on_movie_delete = False
            on_health_issue = False
        elif arr_type.lower() == 'readarr':
            on_grab = True
            on_download = True
            on_upgrade = True
            on_rename = True
            on_book_delete = False
            on_author_delete = False
            on_health_issue = False
        else:
            return {"success": False, "message": f"Unknown ARR type: {arr_type}"}
        
        # Build the notification payload
        notification_data = {
            "onGrab": on_grab,
            "onDownload": on_download,
            "onUpgrade": on_upgrade,
            "onRename": on_rename,
            "onSeriesDelete": on_series_delete if arr_type.lower() == 'sonarr' else False,
            "onEpisodeFileDelete": on_episode_file_delete if arr_type.lower() == 'sonarr' else False,
            "onMovieDelete": on_movie_delete if arr_type.lower() == 'radarr' else False,
            "onBookDelete": on_book_delete if arr_type.lower() == 'readarr' else False,
            "onAuthorDelete": on_author_delete if arr_type.lower() == 'readarr' else False,
            "onHealthIssue": on_health_issue,
            "onApplicationUpdate": False,
            "includeHealthWarnings": False,
            "name": name,
            "implementation": "Webhook",
            "configContract": "WebhookSettings",
            "fields": [
                {
                    "name": "url",
                    "value": webhook_url
                },
                {
                    "name": "method",
                    "value": "1"  # POST
                },
                {
                    "name": "username",
                    "value": ""
                },
                {
                    "name": "password",
                    "value": ""
                }
            ]
        }
        
        # Add type-specific fields
        if arr_type.lower() == 'sonarr':
            notification_data["onSeriesDelete"] = on_series_delete
            notification_data["onEpisodeFileDelete"] = on_episode_file_delete
        elif arr_type.lower() == 'radarr':
            notification_data["onMovieDelete"] = on_movie_delete
        elif arr_type.lower() == 'readarr':
            notification_data["onBookDelete"] = on_book_delete
            notification_data["onAuthorDelete"] = on_author_delete
        
        api_url = f"{arr_url.rstrip('/')}/api/v3/notification"
        headers = {
            'X-Api-Key': arr_api_key,
            'Content-Type': 'application/json'
        }
        
        response = requests.post(api_url, json=notification_data, headers=headers, timeout=10)
        response.raise_for_status()
        
        logger.info(f"Successfully created webhook for {arr_type} at {arr_url}")
        return {"success": True, "message": f"Webhook created successfully"}
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to create webhook: {str(e)}"
        if hasattr(e.response, 'text'):
            error_msg += f" - {e.response.text}"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}
    except Exception as e:
        logger.error(f"Error creating webhook for {arr_type}: {e}", exc_info=True)
        return {"success": False, "message": f"Error: {str(e)}"}


def test_arr_connection(arr_url: str, arr_api_key: str, arr_type: str = "sonarr") -> Dict[str, Any]:
    """
    Test connection to an ARR instance.
    
    Args:
        arr_url: Base URL of the ARR instance
        arr_api_key: API key for the ARR instance
        arr_type: 'sonarr', 'radarr', or 'readarr'
    
    Returns:
        Dict with 'success' bool and 'message' string
    """
    try:
        api_url = f"{arr_url.rstrip('/')}/api/v3/system/status"
        headers = {'X-Api-Key': arr_api_key}
        
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        version = data.get('version', 'Unknown')
        
        logger.info(f"Successfully connected to {arr_type} at {arr_url} (version {version})")
        return {
            "success": True,
            "message": f"Connected successfully (version {version})",
            "version": version
        }
    except requests.exceptions.RequestException as e:
        error_msg = f"Connection failed: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            if e.response.status_code == 401:
                error_msg = "Authentication failed - check API key"
            elif e.response.status_code == 404:
                error_msg = "API endpoint not found - check URL"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}
    except Exception as e:
        logger.error(f"Error testing {arr_type} connection: {e}", exc_info=True)
        return {"success": False, "message": f"Error: {str(e)}"}


def setup_webhook_for_instance(arr_url: str, arr_api_key: str, bot_url: str, arr_type: str = "sonarr", name: Optional[str] = None, auto_create: bool = True) -> Dict[str, Any]:
    """
    Test connection and optionally create webhook for an ARR instance.
    
    Args:
        arr_url: Base URL of the ARR instance
        arr_api_key: API key for the ARR instance
        bot_url: Base URL of the bot
        arr_type: 'sonarr', 'radarr', or 'readarr'
        name: Optional name for the webhook
        auto_create: If True, automatically create webhook if it doesn't exist
    
    Returns:
        Dict with 'success', 'message', 'connection_test', and 'webhook_created' keys
    """
    result = {
        "success": False,
        "message": "",
        "connection_test": None,
        "webhook_created": False,
        "webhook_url": None
    }
    
    # Test connection first
    connection_test = test_arr_connection(arr_url, arr_api_key, arr_type)
    result["connection_test"] = connection_test
    
    if not connection_test["success"]:
        result["message"] = f"Connection test failed: {connection_test['message']}"
        return result
    
    # Get webhook URL
    webhook_url = get_webhook_url(arr_type, bot_url)
    result["webhook_url"] = webhook_url
    
    if not auto_create:
        result["success"] = True
        result["message"] = f"Connection successful. Webhook URL: {webhook_url}"
        return result
    
    # Check if webhook already exists
    existing = get_existing_webhook(arr_url, arr_api_key, webhook_url, arr_type)
    if existing:
        result["success"] = True
        result["message"] = f"Connection successful. Webhook already exists."
        result["webhook_created"] = False
        return result
    
    # Create webhook
    webhook_result = create_webhook(arr_url, arr_api_key, webhook_url, arr_type, name)
    result["webhook_created"] = webhook_result["success"]
    
    if webhook_result["success"]:
        result["success"] = True
        result["message"] = f"Connection successful and webhook created."
    else:
        result["success"] = False
        result["message"] = f"Connection successful but webhook creation failed: {webhook_result['message']}"
    
    return result

