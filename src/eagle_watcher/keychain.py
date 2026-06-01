"""macOS Keychain storage for sensitive tokens using security CLI"""

import logging
import subprocess

_LOG = logging.getLogger("keychain")

SERVICE = "eagle-watcher"
ACCOUNT = "eagle-token"


def get_token(service: str = SERVICE, account: str = ACCOUNT) -> str:
    """Read token from macOS Keychain. Returns empty string if not found."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _LOG.warning("Keychain read failed: %s", e)
    return ""


def set_token(token: str, service: str = SERVICE, account: str = ACCOUNT) -> bool:
    """Store token in macOS Keychain. Returns True on success."""
    try:
        # Use -U to update existing if already exists
        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", account, "-w", token, "-U"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _LOG.warning("Keychain write failed: %s", e)
        return False


def delete_token(service: str = SERVICE, account: str = ACCOUNT) -> bool:
    """Delete token from macOS Keychain. Returns True on success."""
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _LOG.warning("Keychain delete failed: %s", e)
        return False