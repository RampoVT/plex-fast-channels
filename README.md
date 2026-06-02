# 🚫 SOME REGIONS MY BE GEO BLOCKED 

# plex-alt-fast-channels

Auto-generated Plex Free Live TV playlists and EPG/XMLTV data, rebuilt every 6 hours via GitHub Actions.

> **No third-party aggregators.** All data is fetched directly from the Plex API using anonymous tokens.  
> EPG is embedded in each playlist via `url-tvg=` — most apps load it automatically.

---

## Playlists

| Flag | Region | M3U |
|------|--------|-----|
| 🇺🇸 | United States | [plex_us.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_us.m3u) |
| 🇨🇦 | Canada | [plex_ca.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_ca.m3u) |
| 🇬🇧 | United Kingdom | [plex_gb.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_gb.m3u) |
| 🇦🇺 | Australia | [plex_au.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_au.m3u) |
| 🇳🇿 | New Zealand | [plex_nz.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_nz.m3u) |
| 🇲🇽 | Mexico | [plex_mx.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_mx.m3u) |
| 🇪🇸 | Spain | [plex_es.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_es.m3u) |
| 🇫🇷 | France | [plex_fr.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_fr.m3u) |
| 🇩🇪 | Germany | [plex_de.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_de.m3u) |
| 🇧🇷 | Brazil | [plex_br.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_br.m3u) |
| 🇮🇳 | India | [plex_in.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_in.m3u) |
| 🇯🇵 | Japan | [plex_jp.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_jp.m3u) |
| 🇰🇷 | South Korea | [plex_kr.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_kr.m3u) |
| 🇸🇪 | Sweden | [plex_se.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_se.m3u) |
| 🇳🇱 | Netherlands | [plex_nl.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_nl.m3u) |
| 🌍 | All Regions | [plex_all.m3u](https://raw.githubusercontent.com/BuddyChewChew/plex-alt-fast-channels/main/playlists/plex_all.m3u) |

---

## How it works

```
get_anonymous_token(region)
    └─ POST clients.plex.tv/api/v2/users/anonymous
           (X-Forwarded-For spoofed to region IP for correct lineup)

fetch_channels(token, region)  →  cached in channels.json (refreshed weekly)
    ├─ GET epg.provider.plex.tv/               → genre slugs
    └─ GET epg.provider.plex.tv/lineups/plex/channels?genre={slug}
           (repeated for each genre; channels de-duplicated by gridKey)

build_m3u(channels, token, region)
    └─ Stream URL: https://epg.provider.plex.tv{channel.key}?X-Plex-Token={token}

build_epg(channels, token, region)
    └─ GET epg.provider.plex.tv/grid?channelGridKey={gk}&date={YYYY-MM-DD}
           (fetched concurrently; written as gzip-compressed .xml.gz)
           plex_all.xml.gz merges all per-region EPG files into one
```

- Tokens are fetched fresh every run (~6 hour expiry, matches the cron schedule)
- Channel metadata is cached in `channels.json` and only re-fetched weekly or on `--refresh-channels`
- DRM-protected channels are automatically skipped

---

## Run locally

```bash
git clone https://github.com/BuddyChewChew/plex-alt-fast-channels.git
cd plex-alt-fast-channels
pip install requests urllib3

python generate.py                        # all regions
python generate.py --regions us ca gb     # specific regions
python generate.py --no-epg              # skip EPG (faster)
python generate.py --days 3              # 3 days of EPG
python generate.py --refresh-channels    # force re-fetch channel list
```

Output files land in `./playlists/`.

---

## License

MIT
