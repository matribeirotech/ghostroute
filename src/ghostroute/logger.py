"""Configuração de logging."""
from __future__ import annotations

import logging
import sys


def setup_logging(verbosity: int = 0, quiet: bool = False) -> None:
    if quiet:
        level = logging.WARNING
    elif verbosity >= 1:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

