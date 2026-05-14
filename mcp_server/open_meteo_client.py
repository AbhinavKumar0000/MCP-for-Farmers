"""Shared Open-Meteo HTTP client with retries and typed errors."""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_SESSION = requests.Session()


class OpenMeteoError(RuntimeError):
    """Raised when an Open-Meteo request fails after retries."""


class OpenMeteoClient:
    """Small wrapper around a shared requests.Session with retry logic."""

    def __init__(self, base_url: str, session: requests.Session, timeout: int = 15) -> None:
        self._base_url = base_url
        self._session = session
        self._timeout = timeout

    def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        delay_seconds = 1.0
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                response = self._session.get(self._base_url, params=params, timeout=self._timeout)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Open-Meteo request failed on attempt %s/%s for %s: %s",
                    attempt,
                    3,
                    self._base_url,
                    exc,
                )
                if attempt == 3:
                    break
                time.sleep(delay_seconds)
                delay_seconds *= 2

        raise OpenMeteoError(f"Open-Meteo request failed after 3 attempts: {last_error}") from last_error


forecast_client = OpenMeteoClient(FORECAST_URL, _SESSION, timeout=12)
archive_client = OpenMeteoClient(ARCHIVE_URL, _SESSION, timeout=20)
