"""
Utility functions for the Plex Discord Bot.
"""
import json
import os
import re
import logging
from dotenv import load_dotenv
from typing import Any, Dict
from config import update_config

# Load environment variables from .env file at the very start
load_dotenv()

logger = logging.getLogger(__name__)


def load_config(config_file_path: str = "config.json") -> Dict[str, Any]:
    """
    Loads configuration from a JSON file, replacing environment variable placeholders,
    and updates the shared application config.
    """
    try:
        with open(config_file_path, 'r') as f:
            config_data = json.load(f)
        logger.info(f"Successfully opened and loaded '{config_file_path}'.")
    except FileNotFoundError:
        logger.error(f"Config file '{config_file_path}' not found.")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing config file '{config_file_path}': {e}.")
        raise

    processed_config = _replace_placeholders(config_data)
    update_config(processed_config)
    logger.info("Shared application configuration updated.")

    return processed_config


def _replace_placeholders(obj: Any) -> Any:
    """
    Recursively replaces placeholder strings like "${ENV_VAR_NAME}"
    with their actual environment variable values.
    """
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_replace_placeholders(elem) for elem in obj]
    elif isinstance(obj, str):
        match = re.fullmatch(r'\$\{(\w+)\}', obj)
        if match:
            env_var_name = match.group(1)
            value = os.getenv(env_var_name)
            if value is None:
                logger.warning(
                    f"Environment variable '{env_var_name}' in config is not set. "
                    f"Using placeholder '{obj}' as fallback.")
                return obj
            logger.debug(f"Replaced placeholder for '{env_var_name}'.")
            return value
    return obj
