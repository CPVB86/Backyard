import logging


def configure_logging(level: str) -> None:
    logger = logging.getLogger("backyard")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
