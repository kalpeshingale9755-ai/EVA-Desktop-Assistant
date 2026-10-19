"""Simple logger: prints to console and writes to logs/eva.log."""
import logging
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def get_logger(name: str) -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = logging.FileHandler(LOG_DIR / "eva.log", encoding="utf-8")
    file.setFormatter(fmt)
    logger.addHandler(console)
    logger.addHandler(file)
    return logger
