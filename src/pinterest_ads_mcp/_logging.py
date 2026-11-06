"""Logging for pinterest-ads-mcp.

Stays on the standard library. Uses one named logger, 'pinterest_ads_mcp', which
writes to stderr so the stdio MCP transport keeps stdout clean for JSON-RPC.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "pinterest_ads_mcp"

_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger or a child of it."""
    base = logging.getLogger(LOGGER_NAME)
    if not base.handlers:
        configure_default()
    if name and name != LOGGER_NAME:
        return base.getChild(name)
    return base


def configure_default() -> None:
    """Add a stderr handler once. The level comes from PINTEREST_ADS_LOG_LEVEL (default INFO)."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    level_name = os.environ.get("PINTEREST_ADS_LOG_LEVEL", "INFO").upper()
    logger.setLevel(_LEVELS.get(level_name, logging.INFO))
    logger.propagate = False
