"""
Network utility helpers for PrintSVC.
"""
import logging
import socket

logger = logging.getLogger("PrintSVC")

# Cache for local IP to avoid repeated DNS lookups
_local_ip_cache = None


def get_local_ip():
    """Get the primary LAN IP address of this machine."""
    global _local_ip_cache
    if _local_ip_cache:
        return _local_ip_cache

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Use a public DNS address — no actual connection is made
        s.connect(("8.8.8.8", 80))
        _local_ip_cache = s.getsockname()[0]
        s.close()
    except Exception:
        _local_ip_cache = "127.0.0.1"
    return _local_ip_cache


def clear_local_ip_cache():
    """Clear the cached local IP (call after network changes)."""
    global _local_ip_cache
    _local_ip_cache = None
