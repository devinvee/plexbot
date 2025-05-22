import json
import os
import re
from dotenv import load_dotenv

# Load environment variables from .env file at the very start
# This ensures os.getenv() calls later will find them.
load_dotenv()


def load_config(config_file_path="config.json"):
    """
    Loads configuration from a JSON file, replacing placeholder strings
    like "${ENV_VAR_NAME}" with their actual environment variable values.
    """
    config = {}
    try:
        with open(config_file_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file '{config_file_path}' not found.")
    except json.JSONDecodeError as e:
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
                    # You might want to raise an error here if a critical env var is missing
                    print(
                        f"Warning: Environment variable '{env_var_name}' referenced in config.json is not set. Using placeholder.")
                    return obj  # Return the placeholder string if not found
                return value
            return obj
        else:
            return obj

    return replace_placeholders(config)
