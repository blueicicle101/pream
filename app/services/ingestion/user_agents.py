"""
User-Agent utilities for provider requests and stream proxying.
Provides sticky UA per source or randomized realistic IPTV/media player UAs.
"""
import random
from typing import Optional

# Curated list of realistic IPTV client and media player User-Agents
USER_AGENTS = [
    # TiviMate
    "TiviMate/4.7.0 (Linux; Android 11; SHIELD Android TV Build/RQ1A.210125.001)",
    "TiviMate/4.6.1 (Linux; Android 9; AFTMM Build/PS7273)",
    "TiviMate/5.0.4 (Linux; Android 12; Chromecast Build/STTE.220920.015.A1)",
    # VLC
    "VLC/3.0.20 LibVLC/3.0.20",
    "VLC/3.0.18 LibVLC/3.0.18",
    # Kodi
    "Kodi/20.2 (Windows NT 10.0; Win64; x64) App_Bitness/64 Version/20.2-Git:20230629-5f418d1a13",
    "Kodi/21.0 (Linux; Android 11; SHIELD Android TV) App_Bitness/64 Version/21.0",
    # Apple TV / GSE Smart IPTV
    "GSE SMART IPTV/8.1 (Apple TV; tvOS 16.5)",
    "GSE SMART IPTV/7.4 (iOS; iPhone 13; iOS 16.1.1)",
    # IPTV Smarters Pro
    "IPTV Smarters Pro/3.1.5.1 (Android; Phone)",
    "IPTV Smarters Pro/4.0.2 (Samsung Tizen; SmartTV)",
    # OTT Navigator
    "OTT Navigator/1.6.8.4 (Linux; Android 10; Mi Box S)",
    "OTT Navigator/1.6.9.1 (Linux; Android 11; Fire TV Stick 4K Max)",
    # ExoPlayer
    "ExoPlayerLib/2.18.5",
    "ExoPlayerLib/2.19.1",
    # iMPlayer
    "iMPlayer/2.0.0 (Android; Phone)",
    # Desktop Browser
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def get_random_user_agent() -> str:
    """Return a randomly chosen realistic User-Agent string."""
    return random.choice(USER_AGENTS)


def get_user_agent(custom_ua: Optional[str] = None) -> str:
    """
    Get the User-Agent to use for a request.
    If custom_ua is provided and non-empty, use it (sticky UA).
    Otherwise, choose a random one.
    """
    if custom_ua and custom_ua.strip():
        return custom_ua.strip()
    return get_random_user_agent()
