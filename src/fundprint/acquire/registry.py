"""Scraper registry: maps source_family strings to scraper classes.

Scrapers self-register via the @register decorator so run_acquire.py
never needs to import each module explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fundprint.acquire.base import Scraper

_registry: dict[str, type["Scraper"]] = {}


def register(scraper_cls: type["Scraper"]) -> type["Scraper"]:
    """Decorator that adds scraper_cls to the registry under its source_family."""
    key = scraper_cls.source_family
    if key in _registry:
        raise ValueError(f"source_family {key!r} is already registered")
    _registry[key] = scraper_cls
    return scraper_cls


def get(source_family: str) -> type["Scraper"]:
    """Return the scraper class registered under source_family."""
    try:
        return _registry[source_family]
    except KeyError:
        available = ", ".join(sorted(_registry))
        raise KeyError(
            f"No scraper registered for {source_family!r}. Available: {available}"
        )
