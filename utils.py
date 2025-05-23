import json
import os
import re
import logging  # Import logging
from dotenv import load_dotenv

# Load environment variables from .env file at the very start
# This ensures os.getenv() calls later will find them.
load_dotenv()

# Get a logger for this module.
# Note: Basic logging config is usually done in the main script.
# If this utility is called very early, it might use Python's default logger
# until main.py's logging.basicConfig takes effect.
logger = logging.getLogger(__name__)


def load_config(config_file_path="config.json"):
    """
    Loads configuration from a JSON file, replacing placeholder strings
    like "${ENV_VAR_NAME}" with their actual environment variable values.
    """
    config = {}
    try:
        with open(config_file_path, 'r') as f:
            config = json.load(f)
        # Info level
        logger.info(f"Successfully opened and loaded '{config_file_path}'.")
    except FileNotFoundError:
        # Use logger.error as this is a critical failure.
        # It will likely be caught by the calling script and cause exit.
        logger.error(f"Config file '{config_file_path}' not found.")
        raise FileNotFoundError(f"Config file '{config_file_path}' not found.")
    except json.JSONDecodeError as e:
        logger.error(
            f"Error parsing config file '{config_file_path}': {e.msg} at position {e.pos}.")
        raise json.JSONDecodeError(
            f"Error parsing config file '{config_file_path}': {e.msg}", e.doc, e.pos)

    # Function to recursively replace placeholders
    def replace_placeholders(obj):
        if isinstance(obj, dict):
            return {k: replace_placeholders(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_placeholders(elem) for elem in obj]
        elif isinstance(obj, str):
            # Check for the placeholder pattern "${ENV_VAR_NAME}"
            match = re.fullmatch(r'\$\{(\w+)\}', obj)
            if match:
                env_var_name = match.group(1)
                value = os.getenv(env_var_name)
                if value is None:
                    # Use logger.warning. This will be captured if logging is set up.
                    # For very early calls, it might default to stderr.
                    logger.warning(
                        f"Environment variable '{env_var_name}' referenced in config.json is not set. "
                        f"Returning placeholder string '{obj}'.")
                    return obj  # Return the placeholder string if not found
                # Debug level
                logger.debug(
                    f"Replaced placeholder '{obj}' with environment variable '{env_var_name}' value.")
                return value
            return obj
        else:
            return obj

    processed_config = replace_placeholders(config)
    logger.info("Configuration placeholders processed.")  # Info level
    # Debug level
    logger.debug(
        f"Final processed config (first 500 chars): {json.dumps(processed_config, indent=2)[:500]}...")
    return processed_config
