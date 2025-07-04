import json
import os
from loguru import logger
from text import t

CONFIG_FILE = 'config.json'

def load_config():
    default_config = {
        'language': 'ru',
        'max_threads': 5,
        'retry_delay': 20,
        'logout_old_session': True,
        'change_2fa': True
    }
    if not os.path.exists(CONFIG_FILE):
        logger.info(t("log.config_file_missing", locale="en", file=CONFIG_FILE))
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
        default_config.update(config)
    return default_config

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
    logger.info(t("log.config_saved", locale="en", file=CONFIG_FILE))