"""Application logging for HotPepperPodcast."""
from __future__ import annotations
import logging
import os
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / "Logs" / "HotPepperPodcast"
LOG_FILENAME = "hotpepperpodcast.log"

def log_path() -> Path:
    return Path(os.environ.get("HPP_LOG_DIR", str(DEFAULT_LOG_DIR))).expanduser() / LOG_FILENAME

def configure_logging(level: int = logging.INFO) -> logging.Logger:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hotpepperpodcast")
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == path.resolve() for h in logger.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(console)
    return logger
