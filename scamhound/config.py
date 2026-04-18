"""
ScamHound Configuration Module

Manages API keys and settings via config.json with fallback to environment
variables. Provides secure masking and status checking without exposing
sensitive values.
"""

import os
import json
import logging
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration file path
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# All configuration keys managed by this module
CONFIG_KEYS = [
    "BAGS_API_KEY",
    "HELIUS_API_KEY",
    "BIRDEYE_API_KEY",
    "BUBBLEMAPS_API_KEY",
    "ANTHROPIC_API_KEY",
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_SECRET",
    "TWITTER_BEARER_TOKEN",
    "RISK_ALERT_THRESHOLD",
    "POLL_INTERVAL_SECONDS",
    "SCAMHOUND_ADMIN_TOKEN",
]

# Default values for non-sensitive settings
DEFAULTS = {
    "RISK_ALERT_THRESHOLD": "65",
    "POLL_INTERVAL_SECONDS": "60",
}


def _mask_value(value: str) -> str:
    """
    Mask a sensitive value showing only the last 4 characters.
    Returns "Not configured" if empty.
    """
    if not value:
        return "Not configured"
    if len(value) <= 4:
        return "••••" + value
    return "••••••" + value[-4:]


def load_config() -> None:
    """
    Load configuration from config.json (if exists) and merge into os.environ.
    Priority: config.json > .env > defaults

    Called at application startup to ensure all config is available via
    os.environ.
    """
    # First, apply defaults for any missing keys
    for key, default_value in DEFAULTS.items():
        if not os.environ.get(key):
            os.environ[key] = default_value
    
    # Then load from config.json if it exists
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            
            # Merge into os.environ (config.json takes highest priority)
            for key in CONFIG_KEYS:
                if key in config_data and config_data[key]:
                    os.environ[key] = str(config_data[key])
            
            logger.info(f"[CONFIG] Loaded configuration from {CONFIG_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"[CONFIG] Failed to parse config.json: {e}")
        except Exception as e:
            logger.error(f"[CONFIG] Error loading config: {e}")
    else:
        logger.info("[CONFIG] No config.json found, using env/defaults")


def _validate_api_key(key: str, value: str) -> tuple[bool, str]:
    """
    Validate API key format.
    
    Args:
        key: The configuration key name
        value: The API key value to validate
        
    Returns:
        tuple[bool, str]: (is_valid, error_message)
    """
    if not value or value.strip() == "":
        return True, ""  # Empty values are skipped, not invalid
    
    if key == "ANTHROPIC_API_KEY":
        if not value.startswith("sk-ant-") or len(value) < 20:
            return False, (
                "ANTHROPIC_API_KEY must start with 'sk-ant-' "
                "and be at least 20 characters"
            )
    elif key == "HELIUS_API_KEY":
        if len(value) < 10:
            return False, (
                "HELIUS_API_KEY must be at least 10 characters (UUID format)"
            )
    elif key == "BAGS_API_KEY":
        if not value.startswith("bags_") or len(value) < 10:
            return False, (
                "BAGS_API_KEY must start with 'bags_' "
                "and be at least 10 characters"
            )
    elif key == "BIRDEYE_API_KEY":
        if len(value) < 10:
            return False, "BIRDEYE_API_KEY must be at least 10 characters"
    elif key == "BUBBLEMAPS_API_KEY":
        if len(value) < 10:
            return False, "BUBBLEMAPS_API_KEY must be at least 10 characters"
    elif key == "SCAMHOUND_ADMIN_TOKEN":
        # No format requirement - any non-empty string is valid
        pass
    
    return True, ""


def save_config(keys: Dict[str, Any]) -> bool:
    """
    Save API keys and settings to config.json and update os.environ.
    
    Args:
        keys: Dictionary of key-value pairs to save
        
    Returns:
        bool: True if saved successfully, False otherwise
        
    Raises:
        ValueError: If any key fails format validation
    """
    try:
        # Load existing config if available
        existing_config = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    existing_config = json.load(f)
            except (json.JSONDecodeError, Exception):
                existing_config = {}
        
        # Validate and collect errors
        validation_errors = []
        valid_keys = {}
        
        for key, value in keys.items():
            if key not in CONFIG_KEYS:
                continue
            
            # Skip if value is empty or unchanged (still masked)
            if not value or value.strip() == "":
                continue
            if value.startswith("••••") and value != "Not configured":
                # Value is masked, don't overwrite
                continue
            
            # Validate key format
            is_valid, error_msg = _validate_api_key(key, value)
            if not is_valid:
                validation_errors.append(error_msg)
            else:
                valid_keys[key] = value
        
        # Raise error if any validation failed
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        
        # Update config with valid keys
        for key, value in valid_keys.items():
            existing_config[key] = value
            os.environ[key] = str(value)
        
        # Write to config.json
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing_config, f, indent=2)
        
        logger.info("[CONFIG] Configuration saved successfully")
        return True
        
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"[CONFIG] Failed to save configuration: {e}")
        return False


def get_masked_keys() -> Dict[str, str]:
    """
    Get all API key values masked as "••••••last4" (or "Not configured" if
    empty). Never returns full key values.
    
    Returns:
        Dict[str, str]: Dictionary of config keys with masked values
    """
    masked = {}
    for key in CONFIG_KEYS:
        value = os.environ.get(key, "")
        masked[key] = _mask_value(value)
    return masked


def get_config_status() -> Dict[str, bool]:
    """
    Get configuration status showing which keys are configured.
    Returns boolean values only, no sensitive data exposed.
    
    Returns:
        Dict[str, bool]: Dictionary of config keys with True/False status
    """
    status = {}
    for key in CONFIG_KEYS:
        value = os.environ.get(key, "")
        status[key] = bool(value and value.strip())
    return status


def get_raw_config() -> Dict[str, str]:
    """
    Get raw configuration values from environment.
    Use with caution - returns actual API key values.
    
    Returns:
        Dict[str, str]: Dictionary of config keys with actual values
    """
    raw = {}
    for key in CONFIG_KEYS:
        raw[key] = os.environ.get(key, "")
    return raw


# Initialize config on module import
load_config()
