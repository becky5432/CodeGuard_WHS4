import logging
import sys

from runner.config import settings


LOG_FORMAT = "%(asctime)s %(levelname)s service=runner %(message)s"


def configure_logging() -> logging.Logger:
    """Runner 전용 로거를 설정하고 반환한다."""
    logger = logging.getLogger("runner")
    level = getattr(logging, settings.log_level)

    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)
    for handler in logger.handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)

    return logger
