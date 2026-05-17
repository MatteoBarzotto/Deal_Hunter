"""One-time interactive Allegro login. Run once, tokens auto-refresh forever after.

Usage:
    python auth_allegro.py
"""
from __future__ import annotations

from scrapers.allegro_auth import authorize_device_interactive


if __name__ == "__main__":
    authorize_device_interactive()
