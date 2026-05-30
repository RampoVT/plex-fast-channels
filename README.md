# plex-alt-fast-channels

Auto-generated Plex Free Live TV playlists and EPG/XMLTV data, rebuilt every 6 hours via GitHub Actions.

> **No third-party aggregators.** All data is fetched directly from the Plex API using anonymous tokens.

---

## Playlist URLs

Replace `{region}` with any code from the table below.

| File | Description |
|------|-------------|
| `playlists/plex_{region}.m3u` | M3U playlist for a single region |
| `playlists/plex_{region}.xml` | XMLTV / EPG for a single region |
| `playlists/plex_all.m3u` | Combined playlist (all regions) |
| `playlists/plex_all.xml` | Combined XMLTV (all regions) |

**Raw base URL:**
```
https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/
```

**Example – United States:**
```
M3U: https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_us.m3u
EPG: https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_us.xml
```

---

## Supported Regions

| Code | Region            |
|------|-------------------|
| `us` | United States     |
| `ca` | Canada            |
| `gb` | United Kingdom    |
| `au` | Australia         |
| `nz` | New Zealand       |
| `mx` | Mexico            |
| `es` | Spain             |
| `fr` | France            |
| `de` | Germany           |
| `br` | Brazil            |
| `in` | India             |
| `jp` | Japan             |
| `kr` | South Korea       |
| `se` | Sweden            |
| `nl` | Netherlands       |

---

## Setup

### 1. Fork / clone this repo

```bash
git clone https://github.com/BuddyChewChew/plex-alt-fast-channels.git
cd plex-alt-fast-channels
```

### 2. Install dependencies

```bash
pip install requests urllib3
```

### 3. Run locally

```bash
# All regions, with EPG (default)
python generate.py

# Specific regions only
python generate.py --regions us ca gb

# Skip EPG (faster)
python generate.py --no-epg

# Fetch 3 days of EPG
python generate.py --days 3

# Custom repo base URL (if you host the XML elsewhere)
python generate.py --repo https://raw.githubusercontent.com/YourFork/plex-alt-fast-channels/main
```

Output files land in `./playlists/`.

### 4. GitHub Actions (automatic)

The workflow `.github/workflows/generate.yml` runs automatically every 6 hours and commits the updated files back to the repo.

You can also trigger it manually from **Actions → Generate Plex Playlists → Run workflow**, with optional overrides for regions, EPG toggle, and EPG days.

---

## How it works

```
get_anonymous_token(region)
    └─ POST clients.plex.tv/api/v2/users/anonymous
           (X-Forwarded-For spoofed to region IP for correct lineup)

fetch_channels(token, region)
    ├─ GET epg.provider.plex.tv/               → genre slugs
    └─ GET epg.provider.plex.tv/lineups/plex/channels?genre={slug}
           (repeated for each genre; channels de-duplicated by gridKey)

build_m3u(channels, token, region)
    └─ Stream URL: https://epg.provider.plex.tv{channel.key}?X-Plex-Token={token}

build_epg(channels, token, region)
    └─ GET epg.provider.plex.tv/grid?channelGridKey={gk}&date={YYYY-MM-DD}
           (fetched concurrently for each channel × day)
```

Tokens expire after ~6 hours; each workflow run fetches fresh ones.  
DRM-protected channels are automatically skipped.

---

## Notes

- Stream URLs embed an anonymous token that expires in ~6 hours.  
  Re-run the generator (or rely on the cron) to refresh them.
- EPG coverage: today + tomorrow by default (`--days 2`).
- The `plex_all.*` files merge all regions; duplicate channels keep the first occurrence.
- GEO spoofing IPs are in `generate.py → GEO_IPS`. Update them if a region stops returning channels.

---

## License

MIT
