"""Cross-process coordination for short Bluetooth recovery operations."""
import asyncio
import os
from pathlib import Path
import re
import sys

import portalocker

ADAPTER_ENV = "OPENDP3_BLE_ADAPTER"
RADIO_LOCK_TIMEOUT = 45.0


def configured_adapter() -> str:
    """Return the explicitly selected Linux adapter, defaulting to hci0."""
    adapter = os.environ.get(ADAPTER_ENV, "hci0").strip()
    if not re.fullmatch(r"hci[0-9]+", adapter):
        raise ValueError(f"Invalid Bluetooth adapter {adapter!r}; expected hci followed by digits.")
    return adapter


def bleak_adapter_kwargs() -> dict[str, str]:
    """Arguments accepted by Bleak's BlueZ scanner/client backends."""
    return {"adapter": configured_adapter()} if sys.platform.startswith("linux") else {}


def radio_lock_path(data_directory: Path) -> Path:
    return Path(data_directory) / f"radio-{configured_adapter()}.lock"


class RadioLease:
    """Serialize scan/connect recovery without serializing healthy sessions."""

    def __init__(self, path: Path, *, timeout: float = RADIO_LOCK_TIMEOUT):
        self.path = Path(path)
        self.timeout = timeout
        self._lock = None

    async def __aenter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = portalocker.Lock(str(self.path), timeout=self.timeout)
        try:
            await asyncio.to_thread(self._lock.acquire)
        except (portalocker.exceptions.LockException, PermissionError, OSError) as exc:
            raise ConnectionError(
                f"Bluetooth recovery lease unavailable for {configured_adapter()} "
                f"({type(exc).__name__})."
            ) from exc
        return self

    async def __aexit__(self, *_):
        if self._lock is not None:
            self._lock.release()
            self._lock = None
