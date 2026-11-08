#!/usr/bin/env python3
"""Forwarder for app.api.v1.map_system."""

from app.api.v1.map_system import (
    Event,
    FixIn,
    FixResult,
    FixSource,
    MapSettings,
    close_map_system,
    emit_listing_event,
    emit_user_event,
    init_map_system,
    install_map_system,
    map_router,
    rt,
    _selftest,
)

__all__ = [
    "map_router",
    "install_map_system",
    "init_map_system",
    "close_map_system",
    "emit_listing_event",
    "emit_user_event",
    "MapSettings",
    "FixIn",
    "FixResult",
    "FixSource",
    "Event",
    "rt",
]

if __name__ == "__main__":
    _selftest()
