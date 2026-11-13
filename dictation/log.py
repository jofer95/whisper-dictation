"""Lightweight file logger so the packaged .app is debuggable.

GUI apps have no console, so we tee everything to a log file the user can share:
    ~/Library/Logs/whisper-dictation-hcp.log
"""

import logging
import os

LOG_PATH = os.path.expanduser("~/Library/Logs/whisper-dictation-hcp.log")

_logger = None


def get_logger():
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("dictation")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        # Explicit UTF-8: a GUI .app has no LANG, so the default encoding is
        # ASCII and every line with an accent or emoji was silently dropped.
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # fall back to stdout only

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _logger = logger
    return logger
