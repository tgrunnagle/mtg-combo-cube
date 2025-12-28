"""Utilities for normalizing requirement identifiers for deduplication."""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def normalize_scryfall_url(scryfall_api: str) -> str:
    """
    Extract and normalize query parameters from a Scryfall API URL.

    - Parses URL query string
    - Removes noise parameters (order, format, etc.)
    - Sorts remaining parameters alphabetically
    - Returns canonical string representation

    Example:
        Input:  "https://api.scryfall.com/cards/search?q=type:creature+keyword:haste&order=edhrec"
        Output: "q=keyword:haste type:creature"
    """
    parsed = urlparse(scryfall_api)
    params = parse_qs(parsed.query)

    # Remove non-essential parameters that don't affect query semantics
    noise_params = {"order", "format", "unique", "include_extras", "include_multilingual"}
    filtered = {k: v for k, v in params.items() if k not in noise_params}

    # Normalize the 'q' parameter specifically (the main query)
    if "q" in filtered:
        # Split query terms, sort them, rejoin
        q_value = filtered["q"][0] if filtered["q"] else ""
        # Handle URL-encoded spaces (+ or %20)
        q_value = q_value.replace("+", " ").replace("%20", " ")
        # Remove legal:commander filter (already stripped in preprocessing)
        q_value = re.sub(r"\s*legal:commander\s*", " ", q_value)
        # Split on spaces, sort, rejoin
        terms = sorted(q_value.split())
        filtered["q"] = [" ".join(terms)]

    # Create canonical representation: sorted key=value pairs
    canonical_parts = []
    for key in sorted(filtered.keys()):
        for value in sorted(filtered[key]):
            canonical_parts.append(f"{key}={value}")

    return "|".join(canonical_parts)


def normalize_template_name(name: str) -> str:
    """
    Normalize a free-form template name for grouping.

    - Lowercase
    - Keep only alphanumeric characters and spaces
    - Normalize whitespace (collapse multiple spaces, strip)

    Example:
        Input:  "Green Persist Creature"
        Output: "green persist creature"
    """
    # Lowercase
    name = name.lower()
    # Keep only alphanumeric and spaces
    name = re.sub(r"[^a-z0-9\s]", "", name)
    # Normalize whitespace
    name = " ".join(name.split())
    return name


def compute_requirement_group_key(scryfall_api: str | None, name: str) -> str:
    """
    Compute a canonical group key for a requirement.

    Args:
        scryfall_api: The Scryfall API URL (if available)
        name: The template name (fallback)

    Returns:
        A canonical string key for grouping identical requirements
    """
    if scryfall_api:
        return f"scryfall:{normalize_scryfall_url(scryfall_api)}"
    else:
        return f"name:{normalize_template_name(name)}"


def prepare_scryfall_url(scryfall_api: str) -> str:
    """
    Prepare a Scryfall API URL for fetching by ensuring proper ordering.

    - Removes the legal:commander filter (we handle legality separately)
    - Ensures order=edhrec is present (for consistent card ranking)

    Args:
        scryfall_api: The original Scryfall API URL

    Returns:
        A cleaned URL ready for fetching

    Example:
        Input:  "https://api.scryfall.com/cards/search?q=type:creature+legal%3Acommander"
        Output: "https://api.scryfall.com/cards/search?q=type%3Acreature&order=edhrec"
    """
    # Remove legal:commander filter (URL-encoded as legal%3Acommander or +legal%3Acommander)
    url = scryfall_api.replace("+legal%3Acommander", "").replace("legal%3Acommander", "")

    # Parse the URL to check/add order parameter
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # Ensure order=edhrec is set
    if "order" not in params:
        params["order"] = ["edhrec"]
    elif params["order"] != ["edhrec"]:
        # Override any existing order with edhrec
        params["order"] = ["edhrec"]

    # Rebuild the URL with the updated params
    # Use doseq=True to handle list values properly
    new_query = urlencode(params, doseq=True)
    new_parsed = parsed._replace(query=new_query)

    return urlunparse(new_parsed)
