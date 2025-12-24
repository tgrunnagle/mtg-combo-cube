"""Blocklist loader for excluding cards from cube consideration."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BLOCKLIST_PATH = "data/blocklist.txt"


def load_blocklist(path: str | None = None) -> frozenset[str]:
    """
    Load card names from blocklist file.

    Args:
        path: Path to blocklist file. If None, uses default path.

    Returns:
        Frozenset of blocked card names (whitespace-stripped, empty lines ignored)

    Raises:
        FileNotFoundError: If the blocklist file does not exist
    """
    blocklist_path = Path(path) if path else Path(DEFAULT_BLOCKLIST_PATH)

    if not blocklist_path.exists():
        raise FileNotFoundError(f"Blocklist file not found: {blocklist_path}")

    with open(blocklist_path, encoding="utf-8") as f:
        cards = frozenset(
            line.strip() for line in f if line.strip() and not line.strip().startswith("#")
        )

    logger.info(f"Loaded {len(cards)} blocked cards from {blocklist_path}")
    return cards
