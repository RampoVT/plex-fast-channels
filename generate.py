#!/usr/bin/env python3
"""
Plex Free Live TV – M3U + XMLTV generator
Repo: https://github.com/BuddyChewChew/plex-alt-fast-channels

Fetches channel lists and EPG data directly from the Plex API with no
dependency on third-party aggregators (matthuisman, etc.).

Per-region files are written to ./playlists/:
  plex_{region}.m3u          – M3U playlist
  plex_{region}.xml          – XMLTV / EPG (today + tomorrow)
  plex_all.m3u               – Combined playlist (all regions)
  plex_all.xml               – Combined XMLTV

Usage:
  python generate.py                    # all configured regions
  python generate.py --regions us ca    # specific regions only
  python generate.py --no-epg           # skip EPG generation
"""

import argparse
import gzip
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_DIR = "playlists"
MAX_WORKERS = 6          # parallel EPG fetch threads
EPG_DAYS = 2             # how many days of EPG to fetch (today + N-1)
REQUEST_TIMEOUT = 30

# Geographic spoofing IPs – one representative IP per region so Plex
# returns the correct channel lineup.  Rotate / update as needed.
GEO_IPS = {
    "us":  "76.81.9.69",        # Los Angeles, CA
    "ca":  "192.206.151.131",   # Toronto, ON
    "gb":  "193.62.157.66",     # London
    "au":  "110.33.122.75",     # Sydney
    "nz":  "203.86.207.83",     # Auckland
    "mx":  "200.68.128.83",     # Mexico City
    "es":  "88.26.241.248",     # Madrid
    "fr":  "176.31.84.249",     # Paris
    "de":  "217.0.117.58",      # Frankfurt
    "br":  "177.75.44.30",      # São Paulo
    "in":  "49.36.80.10",       # Mumbai
    "jp":  "126.82.100.100",    # Tokyo
    "kr":  "175.209.10.10",     # Seoul
    "se":  "94.254.2.100",      # Stockholm
    "nl":  "77.249.128.10",     # Amsterdam
}

REGION_NAMES = {
    "us":  "United States",
    "ca":  "Canada",
    "gb":  "United Kingdom",
    "au":  "Australia",
    "nz":  "New Zealand",
    "mx":  "Mexico",
    "es":  "Spain",
    "fr":  "France",
    "de":  "Germany",
    "br":  "Brazil",
    "in":  "India",
    "jp":  "Japan",
    "kr":  "South Korea",
    "se":  "Sweden",
    "nl":  "Netherlands",
}

# Base Plex Web headers – mimic a real browser session
BASE_HEADERS = {
    "Accept":                   "application/json, text/plain, */*",
    "Accept-Language":          "en",
    "Connection":               "keep-alive",
    "Origin":                   "https://app.plex.tv",
    "Referer":                  "https://app.plex.tv/",
    "Sec-Fetch-Dest":           "empty",
    "Sec-Fetch-Mode":           "cors",
    "Sec-Fetch-Site":           "same-site",
    "User-Agent":               "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/132.0.0.0 Safari/537.36",
    "sec-ch-ua":                '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
    "sec-ch-ua-mobile":         "?0",
    "sec-ch-ua-platform":       '"Windows"',
}

BASE_PARAMS = {
    "X-Plex-Product":               "Plex Web",
    "X-Plex-Version":               "4.145.1",
    "X-Plex-Platform":              "Chrome",
    "X-Plex-Platform-Version":      "132.0",
    "X-Plex-Features":              "external-media,indirect-media,hub-style-list",
    "X-Plex-Model":                 "standalone",
    "X-Plex-Device":                "Windows",
    "X-Plex-Device-Screen-Resolution": "1920x1080",
    "X-Plex-Provider-Version":      "7.2",
    "X-Plex-Text-Format":           "plain",
    "X-Plex-Language":              "en",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("plex-gen")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(retries: int = 3, backoff: float = 2.0) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _new_client_id() -> str:
    return str(uuid.uuid4()).replace("-", "")


def cleanup_output_dir():
    if os.path.exists(OUTPUT_DIR):
        logger.info("Cleaning %s …", OUTPUT_DIR)
        for name in os.listdir(OUTPUT_DIR):
            fp = os.path.join(OUTPUT_DIR, name)
            try:
                if os.path.isfile(fp) or os.path.islink(fp):
                    os.unlink(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp)
            except Exception as exc:
                logger.warning("Could not delete %s: %s", fp, exc)
    else:
        os.makedirs(OUTPUT_DIR)


def write_file(filename: str, content: str):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(content)
    logger.info("Wrote %s (%d bytes)", filename, len(content))


def _sanitize(text: str) -> str:
    """Strip XML-illegal control characters."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")


# ---------------------------------------------------------------------------
# Plex token
# ---------------------------------------------------------------------------

def get_anonymous_token(region: str = "us") -> tuple[str | None, str | None]:
    """
    Obtain an anonymous Plex auth token.
    Returns (token, client_id) or (None, None) on failure.
    """
    client_id = _new_client_id()
    headers = {**BASE_HEADERS}
    params  = {**BASE_PARAMS, "X-Plex-Client-Identifier": client_id}

    ip = GEO_IPS.get(region, GEO_IPS["us"])
    if ip:
        headers["X-Forwarded-For"] = ip

    session = _make_session()
    for attempt in range(4):
        try:
            resp = session.post(
                "https://clients.plex.tv/api/v2/users/anonymous",
                headers=headers,
                params=params,
                timeout=15,
            )
            if resp.status_code == 429:
                wait = (2 ** attempt) * 10 + random.uniform(0, 5)
                logger.warning("429 on token (attempt %d) – waiting %.0fs", attempt + 1, wait)
                time.sleep(wait)
                continue
            if resp.status_code not in (200, 201):
                logger.warning("Token HTTP %d for %s: %s", resp.status_code, region, resp.text[:120])
                time.sleep(5)
                continue
            token = resp.json().get("authToken")
            if token:
                logger.info("Got anonymous token for region=%s", region)
                return token, client_id
        except Exception as exc:
            logger.warning("Token attempt %d failed (%s): %s", attempt + 1, region, exc)
            time.sleep(5)
    session.close()
    logger.error("Failed to get token for region=%s", region)
    return None, None


# ---------------------------------------------------------------------------
# Channel discovery
# ---------------------------------------------------------------------------

def _fetch_genres(token: str, client_id: str, ip: str) -> dict[str, str]:
    """
    GET https://epg.provider.plex.tv/
    Returns {genre_slug: genre_title}.
    """
    headers = {
        **BASE_HEADERS,
        "X-Forwarded-For": ip,
    }
    params = {
        **BASE_PARAMS,
        "X-Plex-Token":             token,
        "X-Plex-Client-Identifier": client_id,
    }
    session = _make_session()
    try:
        resp = session.get(
            "https://epg.provider.plex.tv/",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("Genre API HTTP %d", resp.status_code)
            return {}
        data = resp.json()
        genres: dict[str, str] = {}
        for feature in data.get("MediaProvider", {}).get("Feature", []):
            if "GridChannelFilter" in feature:
                for g in feature["GridChannelFilter"]:
                    genres[g["identifier"]] = g["title"]
                break
        logger.info("Discovered %d genre slugs", len(genres))
        return genres
    except Exception as exc:
        logger.warning("Genre fetch error: %s", exc)
        return {}
    finally:
        session.close()


def _fetch_channels_for_genre(
    genre_slug: str,
    token: str,
    client_id: str,
    ip: str,
) -> list[dict]:
    """
    GET https://epg.provider.plex.tv/lineups/plex/channels?genre={slug}
    Returns list of channel dicts.
    """
    url = f"https://epg.provider.plex.tv/lineups/plex/channels?genre={genre_slug}"
    headers = {
        **BASE_HEADERS,
        "X-Forwarded-For": ip,
    }
    params = {
        **BASE_PARAMS,
        "X-Plex-Token":             token,
        "X-Plex-Client-Identifier": client_id,
    }
    session = _make_session()
    try:
        resp = session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("Channels HTTP %d for genre=%s", resp.status_code, genre_slug)
            return []
        channels_raw = resp.json().get("MediaContainer", {}).get("Channel", [])
        results = []
        for ch in channels_raw:
            # Skip DRM-protected channels (can't be played without a Plex subscription)
            if any(media.get("drm") for media in ch.get("Media", [])):
                continue
            key_values = [
                part["key"]
                for media in ch.get("Media", [])
                for part in media.get("Part", [])
            ]
            plex_key = key_values[0] if key_values else ""
            results.append({
                "id":       ch.get("id", ""),
                "slug":     ch.get("slug", ""),
                "gridKey":  ch.get("gridKey", ""),
                "name":     ch.get("title", ""),
                "logo":     ch.get("thumb", ""),
                "callSign": ch.get("callSign", ""),
                "key":      plex_key,
            })
        return results
    except Exception as exc:
        logger.warning("Channel fetch error for genre=%s: %s", genre_slug, exc)
        return []
    finally:
        session.close()


def fetch_channels(token: str, client_id: str, region: str) -> dict[str, dict]:
    """
    Discover all channels for *region* by iterating genres.
    Returns {gridKey: channel_dict}.
    """
    ip = GEO_IPS.get(region, GEO_IPS["us"])
    genres = _fetch_genres(token, client_id, ip)
    if not genres:
        # Fallback: try the default genre slugs Plex always has
        genres = {
            "all":   "All",
            "news":  "News",
            "sports":"Sports",
            "movies":"Movies",
        }

    channels: dict[str, dict] = {}
    for slug, title in genres.items():
        raw = _fetch_channels_for_genre(slug, token, client_id, ip)
        for ch in raw:
            gk = ch["gridKey"]
            if gk not in channels:
                channels[gk] = {**ch, "genres": [title]}
            else:
                channels[gk]["genres"].append(title)
        time.sleep(0.2)  # gentle throttle

    logger.info("Region %s – %d unique channels", region, len(channels))
    return channels


# ---------------------------------------------------------------------------
# M3U generation
# ---------------------------------------------------------------------------

def build_m3u(
    channels: dict[str, dict],
    token: str,
    region: str,
    repo_base: str,
) -> str:
    """Build M3U playlist content for *region*."""
    region_name = REGION_NAMES.get(region, region.upper())
    epg_url = f"{repo_base}/playlists/plex_{region}.xml"

    lines = [f'#EXTM3U url-tvg="{epg_url}"\n']

    for gk, ch in sorted(channels.items(), key=lambda x: x[1].get("name", "").lower()):
        name   = _sanitize(ch.get("name", gk))
        logo   = ch.get("logo", "")
        genres = ch.get("genres", [])
        group  = genres[0] if genres else region_name
        plex_key = ch.get("key", "")

        if not plex_key:
            continue  # no stream URL → skip

        stream_url = f"https://epg.provider.plex.tv{plex_key}?X-Plex-Token={token}"

        extinf = (
            f'#EXTINF:-1 '
            f'channel-id="plex-{gk}" '
            f'tvg-id="{gk}" '
            f'tvg-name="{name.replace(chr(34), chr(39))}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group.replace(chr(34), chr(39))}",{name}\n'
        )
        lines.append(extinf)
        lines.append(stream_url + "\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# EPG / XMLTV generation
# ---------------------------------------------------------------------------

def _fetch_epg_for_channel(
    channel: dict,
    date_str: str,
    token: str,
    client_id: str,
    ip: str,
) -> str | None:
    """
    GET https://epg.provider.plex.tv/grid?channelGridKey={gk}&date={YYYY-MM-DD}
    Returns raw XML string (with the <?xml...?> header stripped) or None.
    """
    headers = {
        **BASE_HEADERS,
        "X-Forwarded-For":          ip,
        "x-plex-client-identifier": client_id,
        "x-plex-platform-version":  "132.0",
        "x-plex-provider-version":  "7.2",
        "x-plex-token":             token,
        "x-plex-version":           "4.145.1",
    }
    params = {
        "channelGridKey": channel["gridKey"],
        "date":           date_str,
    }
    session = _make_session(retries=3, backoff=2.0)
    try:
        resp = session.get(
            "https://epg.provider.plex.tv/grid",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("Content-Type", "")
        if "xml" not in ct:
            return None
        xml = resp.text
        xml = re.sub(r'<\?xml[^>]*\?>', "", xml).strip()
        return _sanitize(xml)
    except Exception:
        return None
    finally:
        session.close()


def build_epg(
    channels: dict[str, dict],
    token: str,
    client_id: str,
    region: str,
    days: int = EPG_DAYS,
) -> str:
    """Build XMLTV EPG content for *region* covering *days* days."""
    ip = GEO_IPS.get(region, GEO_IPS["us"])
    today = datetime.now(timezone.utc)
    dates = [(today + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(days)]

    # Collect channel XML fragments + programme blocks in parallel
    channel_xml_parts: list[str] = []
    programme_xml_parts: list[str] = []

    # Build channel entries
    for gk, ch in channels.items():
        name  = escape(_sanitize(ch.get("name", gk)))
        logo  = escape(ch.get("logo", ""))
        cxml  = f'  <channel id="{gk}">\n'
        cxml += f'    <display-name>{name}</display-name>\n'
        if logo:
            cxml += f'    <icon src="{logo}" />\n'
        cxml += '  </channel>\n'
        channel_xml_parts.append(cxml)

    # Fetch programme data concurrently
    tasks = [
        (ch, date)
        for ch in channels.values()
        for date in dates
    ]

    logger.info("Fetching EPG for %s – %d channel/day combos …", region, len(tasks))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_epg_for_channel, ch, date, token, client_id, ip): (ch, date)
            for ch, date in tasks
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                programme_xml_parts.append(result)

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n',
        '<tv generator-info-name="plex-alt-fast-channels" '
        'generator-info-url="https://github.com/BuddyChewChew/plex-alt-fast-channels">\n',
        *channel_xml_parts,
        *programme_xml_parts,
        '</tv>\n',
    ]
    return "".join(xml_lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Generate Plex free live TV playlists")
    parser.add_argument(
        "--regions", nargs="+",
        default=list(GEO_IPS.keys()),
        help="Regions to generate (default: all)",
    )
    parser.add_argument(
        "--no-epg", action="store_true",
        help="Skip EPG generation",
    )
    parser.add_argument(
        "--days", type=int, default=EPG_DAYS,
        help=f"Days of EPG to fetch (default: {EPG_DAYS})",
    )
    parser.add_argument(
        "--repo", default="https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main",
        help="Raw base URL of the repo (used in url-tvg links)",
    )
    return parser.parse_args()


def generate_region(region: str, args) -> dict | None:
    """Full pipeline for one region.  Returns channel dict or None."""
    logger.info("=== Region: %s ===", region.upper())

    token, client_id = get_anonymous_token(region)
    if not token:
        logger.error("Skipping %s – no token", region)
        return None

    channels = fetch_channels(token, client_id, region)
    if not channels:
        logger.warning("No channels found for %s", region)
        return None

    # M3U
    m3u = build_m3u(channels, token, region, args.repo)
    write_file(f"plex_{region}.m3u", m3u)

    # EPG
    if not args.no_epg:
        xml = build_epg(channels, token, client_id, region, days=args.days)
        write_file(f"plex_{region}.xml", xml)

    return channels


def main():
    args = parse_args()
    cleanup_output_dir()

    all_channels: dict[str, dict] = {}   # merged for plex_all.*
    all_tokens: dict[str, tuple] = {}    # region → (token, client_id)

    for region in args.regions:
        result = generate_region(region, args)
        if result:
            for gk, ch in result.items():
                if gk not in all_channels:
                    all_channels[gk] = ch
            # Keep the US token for the "all" playlist stream URLs
            if region not in all_tokens:
                token, client_id = get_anonymous_token(region)
                if token:
                    all_tokens[region] = (token, client_id)

    # Combined "all" files
    if all_channels:
        logger.info("=== Building plex_all.* ===")
        fallback_region = args.regions[0] if args.regions else "us"
        all_token, all_client_id = all_tokens.get(
            fallback_region,
            get_anonymous_token(fallback_region),
        )
        if all_token:
            all_m3u = build_m3u(all_channels, all_token, "all", args.repo)
            write_file("plex_all.m3u", all_m3u)

            if not args.no_epg:
                # For the combined EPG just concatenate the per-region XMLs
                combined_channels_xml: list[str] = []
                combined_programmes_xml: list[str] = []
                for region in args.regions:
                    fp = os.path.join(OUTPUT_DIR, f"plex_{region}.xml")
                    if os.path.exists(fp):
                        with open(fp, encoding="utf-8") as fh:
                            content = fh.read()
                        # Extract <channel> blocks
                        combined_channels_xml.extend(
                            re.findall(r"<channel\b.*?</channel>", content, re.DOTALL)
                        )
                        # Extract <programme> blocks
                        combined_programmes_xml.extend(
                            re.findall(r"<programme\b.*?</programme>", content, re.DOTALL)
                        )

                all_xml = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
                    '<tv generator-info-name="plex-alt-fast-channels" '
                    'generator-info-url="https://github.com/BuddyChewChew/plex-alt-fast-channels">\n'
                    + "\n".join(combined_channels_xml) + "\n"
                    + "\n".join(combined_programmes_xml) + "\n"
                    + "</tv>\n"
                )
                write_file("plex_all.xml", all_xml)

    logger.info("Done. Files written to ./%s/", OUTPUT_DIR)


if __name__ == "__main__":
    main()
