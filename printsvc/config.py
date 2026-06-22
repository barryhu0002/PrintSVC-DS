"""Configuration management for PrintSVC."""
import json
import logging
import os
import sys

logger = logging.getLogger("PrintSVC")

DEFAULT_CONFIG = {
    "printer_name": "",
    "ipp_port": 631,
    "service_name": "PrintSVC",
    "listen_address": "0.0.0.0",
    "log_file": "printsvc.log",
    "log_level": "INFO",
    "mDNS_enabled": True,
}


def get_config_path():
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(exe_dir, "printsvc.json")


def load_config():
    config = dict(DEFAULT_CONFIG)
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user = json.load(f)
            config.update(user)
            logger.info("Loaded config from %s", config_path)
        except Exception as e:
            logger.warning("Failed to load config %s: %s", config_path, e)
    else:
        logger.info("No config file found at %s, using defaults", config_path)
    return config


def save_config(config, path=None):
    path = path or get_config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("Config saved to %s", path)
        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        return False
