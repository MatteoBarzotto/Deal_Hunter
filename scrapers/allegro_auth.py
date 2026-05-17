"""Allegro OAuth2 — device-code flow with on-disk token persistence.

Why device_code instead of client_credentials? `/offers/listing` returns
AccessDenied for client_credentials tokens on some app configurations, but
accepts user-delegated tokens. Device flow lets a headless CLI app obtain a
user token interactively once, then refresh it forever via refresh_token.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from config import PROJECT_ROOT, get_secrets

_DEVICE_URL = "https://allegro.pl/auth/oauth/device"
_TOKEN_URL = "https://allegro.pl/auth/oauth/token"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_TOKEN_FILE = PROJECT_ROOT / "config" / "allegro_tokens.json"


@dataclass
class _Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # unix timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_Tokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
        )


def _save(tokens: _Tokens) -> None:
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(tokens.to_dict(), indent=2))
    _TOKEN_FILE.chmod(0o600)


def _load() -> _Tokens | None:
    if not _TOKEN_FILE.exists():
        return None
    try:
        return _Tokens.from_dict(json.loads(_TOKEN_FILE.read_text()))
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Allegro token file corrupted ({}), ignoring", e)
        return None


def _credentials() -> tuple[str, str]:
    secrets = get_secrets()
    if not (secrets.allegro_client_id and secrets.allegro_client_secret):
        raise RuntimeError(
            "ALLEGRO_CLIENT_ID / ALLEGRO_CLIENT_SECRET missing from config/.env"
        )
    return secrets.allegro_client_id, secrets.allegro_client_secret


def _user_agent() -> str:
    return get_secrets().allegro_user_agent


def authorize_device_interactive() -> None:
    """Run the interactive device-code flow. Prints user_code + URL, polls until done.

    Intended for use from a CLI (`python auth_allegro.py`). Persists tokens to disk.
    """
    client_id, client_secret = _credentials()
    with httpx.Client(timeout=30, headers={"User-Agent": _user_agent()}) as client:
        resp = client.post(
            _DEVICE_URL,
            auth=(client_id, client_secret),
            data={"client_id": client_id},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        device = resp.json()

        user_code = device["user_code"]
        verification_uri = device.get("verification_uri_complete") or device["verification_uri"]
        device_code = device["device_code"]
        interval = device.get("interval", 5)
        expires_in = device.get("expires_in", 600)

        print("\n" + "=" * 60)
        print(" Open in browser and confirm the code:")
        print(f"   {verification_uri}")
        print(f"\n User code: {user_code}")
        print("=" * 60 + "\n")
        print(f"Waiting for confirmation (timeout in {expires_in}s)...")

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            poll = client.post(
                _TOKEN_URL,
                auth=(client_id, client_secret),
                data={"grant_type": _DEVICE_GRANT, "device_code": device_code},
            )
            if poll.status_code == 200:
                payload = poll.json()
                tokens = _Tokens(
                    access_token=payload["access_token"],
                    refresh_token=payload["refresh_token"],
                    expires_at=time.time() + payload.get("expires_in", 43200),
                )
                _save(tokens)
                print(f"\n✓ Authorized. Tokens saved to {_TOKEN_FILE}")
                return
            error = poll.json().get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error in ("access_denied", "expired_token"):
                raise RuntimeError(f"Authorization failed: {error}")
            poll.raise_for_status()

    raise RuntimeError("Authorization timed out.")


def _refresh(tokens: _Tokens) -> _Tokens:
    client_id, client_secret = _credentials()
    with httpx.Client(timeout=30, headers={"User-Agent": _user_agent()}) as client:
        resp = client.post(
            _TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
        )
        resp.raise_for_status()
        payload = resp.json()
    return _Tokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", tokens.refresh_token),
        expires_at=time.time() + payload.get("expires_in", 43200),
    )


def get_valid_token() -> str:
    """Return a valid access token, refreshing if it's close to expiring."""
    tokens = _load()
    if tokens is None:
        raise RuntimeError(
            "Allegro not authorized yet. Run: `python auth_allegro.py` once to log in."
        )
    if time.time() > tokens.expires_at - 120:  # refresh if expires in <2min
        logger.debug("Allegro access token near expiry — refreshing")
        tokens = _refresh(tokens)
        _save(tokens)
    return tokens.access_token
