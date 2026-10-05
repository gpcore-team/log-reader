# GPCORE Log Reader

[🇷🇺 Русская версия](README.ru.md)

Universal filter for Caddy access logs. Reads JSON-lines logs incrementally, extracts records matching configurable keywords, enriches them with geolocation and download-method information, and writes results to a separate numbered log. Supports verified bot detection (Google, Yandex, Bing, Apple, DuckDuckGo), per-profile configuration, and automatic GeoIP database updates.

Originally written for the [GPCORE](https://gpcore.ru) IW4x community, but generic enough for any Caddy-served file-download monitoring.

## Features

- **Incremental reading.** Offset is persisted to disk; each run processes only new lines.
- **Per-profile configuration.** One engine, many profiles in `profiles/*.toml`.
- **Enrichment.**
  - Country code from GeoIP (GeoLite2-City, auto-downloaded and refreshed).
  - Human-readable duration (`2m 40s`, `1h 2m 5s`).
  - Download speed in MB/s.
  - Filename transformation via regex (short release names).
- **Verified bot detection.**
  - Two-step check: `is-crawler` for User-Agent, then forward-confirmed reverse DNS for IP.
  - Google, Yandex, Bing, Apple, DuckDuckGo.
  - Verified bots go to a separate log, not counted as downloads.
- **IP ignore list.** Skip your own debug requests.
- **Numbered output** with cumulative stats across runs.
- **Automatic GeoLite2 update** every 30 days.
- **Logrotate** friendly (via `copytruncate`).

## Quick start

    git clone https://github.com/gpcore-team/log-reader.git
    cd log-reader
    ./install.sh

Then create a profile:

    cp /opt/log_reader/profiles/example.toml /opt/log_reader/profiles/mysite.toml
    nano /opt/log_reader/profiles/mysite.toml

And run:

    python3 /opt/log_reader/caddy_filter.py --profile mysite

## Requirements

- **OS:** Debian/Ubuntu (tested on Ubuntu 20.04 LTS). Should work on any modern Linux.
- **Python:** 3.8 or newer.
  - Python 3.11+ uses built-in `tomllib`.
  - Python 3.8–3.10 needs `tomli` (installed by `install.sh`).
- **Caddy** configured to write access logs in JSON format.
- **pip3** available for installing dependencies.
- **Optional:** `logrotate` (for log rotation), `acl` package (for granting read access to Caddy logs from a non-root user).

## Installation

### Automated

The `install.sh` script handles everything:

    ./install.sh --help                                # show options
    ./install.sh                                       # interactive
    ./install.sh --profile mysite --with-cron --with-logrotate

Options:

| Flag | Meaning |
|---|---|
| `--install-dir DIR` | Install directory (default `/opt/log_reader`) |
| `--profile NAME` | Profile name for cron entry |
| `--with-cron` | Add cron entry (every 5 min) |
| `--with-logrotate` | Install logrotate config to `/etc/logrotate.d/log-reader` |
| `--skip-deps` | Do not install Python dependencies |
| `--non-interactive` | Skip all questions, use defaults |

The script is idempotent — safe to re-run.

### Manual

If you prefer to do it by hand:

    # 1. Dependencies
    sudo apt install python3-pip
    pip install --user tomli geoip2 is-crawler

    # 2. Create structure
    sudo mkdir -p /opt/log_reader
    sudo chown "$USER":"$USER" /opt/log_reader
    mkdir -p /opt/log_reader/{log,state,backup,profiles}

    # 3. Copy files
    cp caddy_filter.py stats.py bots_view.py reset.sh backup.sh \
       help.txt debug.txt logrotate.conf /opt/log_reader/
    chmod 755 /opt/log_reader/{caddy_filter.py,stats.py,bots_view.py,reset.sh,backup.sh}

    # 4. Grant read access to Caddy logs (replace 'kn' with your user)
    sudo setfacl -m u:kn:r-x /var/log/caddy
    sudo setfacl -m u:kn:r-- /var/log/caddy/access.log
    sudo setfacl -d -m u:kn:r-- /var/log/caddy

    # 5. Create a profile
    cp /opt/log_reader/profiles/example.toml /opt/log_reader/profiles/mysite.toml
    nano /opt/log_reader/profiles/mysite.toml

## Configuration

Profiles live in `/opt/log_reader/profiles/*.toml`. The `example.toml` file is fully documented — copy it and adjust.

Minimal profile:

    [profile]
    name        = "My site downloads"
    site        = "example.com"
    input_log   = "/var/log/caddy/access.log"
    keywords    = ["myfile.zip"]
    output_log  = "log/mysite_dl.log"
    offset_file = "state/mysite_filter_offset"

All paths are relative to `/opt/log_reader/` unless they start with `/`. The `{site}` placeholder is expanded in path templates.

### Available placeholders

In `output_format`:

| Placeholder | Meaning |
|---|---|
| `{ts}` | Timestamp `YYYY-MM-DD HH:MM:SS` |
| `{country}` | Country code (`RU`, `US`, `??`) |
| `{ip}` | Client IP |
| `{duration}` | Human-readable (`45s`, `2m 40s`, `1h 2m 5s`) |
| `{speed}` | Download speed (`3.58 MB/s` or `—`) |
| `{filename}` | Filename (possibly transformed) |
| `{uri}` | Full request URI |
| `{size}` | Response size in bytes |
| `{status}` | HTTP status code |
| `{ua_tag}` | UA classification: `UPD1.2`, `PSH`, or empty |

Format specifiers work: `{ip:<16}`, `{country:>3}`.

### Filename transformation

To shorten long filenames via regex:

    [filename_transform]
    mode    = "short"
    pattern = "r(\\d+)_rawfiles_(v[\\d.]+)\\.exe"
    result  = "{1}_{2}"

For a file `IW4x_Update_r5154_rawfiles_v0.2.36.exe` the result is `r5154_v0.2.36`.

If `mode = "full"` or the pattern doesn't match — the basename is used as-is.

### Rebuilding the log after format changes

`caddy_filter.py` works incrementally and **does not rewrite** existing
records in `output_log`. Changes to output format apply only to **new**
lines. If you modify any of these keys in a profile, rebuild the log to
apply changes to historical records:

- `number_lines` — enable/disable line numbering
- `output_format` — column order or composition
- `filename_transform` — new regex or `mode` switch
- `keywords` — expand or narrow the filter

Rebuild with:

    /opt/log_reader/reset.sh --profile <name>

Manual equivalent:

    rm -f /opt/log_reader/state/<name>_filter_offset
    rm -f /opt/log_reader/state/<name>_filter_stats.json
    rm -f /opt/log_reader/state/<name>_bots_cache.json
    > /opt/log_reader/log/<name>_dl.log
    > /opt/log_reader/log/<name>_bots.log
    python3 /opt/log_reader/caddy_filter.py --profile <name>

**Warning:** rebuild resets line numbers and counters. If you want to
preserve existing numbers, add new records on top of the current log
without rebuilding.

## Usage

    # List profiles
    python3 /opt/log_reader/caddy_filter.py --list

    # Run a profile manually
    python3 /opt/log_reader/caddy_filter.py --profile mysite

    # Statistics (per profile)
    python3 /opt/log_reader/stats.py --profile mysite
    python3 /opt/log_reader/stats.py --profile mysite --verbose

    # View bots log
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log 100
    python3 /opt/log_reader/bots_view.py /opt/log_reader/log/bots_example.com.log 0

    # Full reset (clears logs, state, re-runs from scratch)
    /opt/log_reader/reset.sh --profile mysite

    # Reset all profiles
    /opt/log_reader/reset.sh --profile_all

    # Backup
    /opt/log_reader/backup.sh

## Cron

Add a task per profile:

    */5 * * * * /usr/bin/python3 /opt/log_reader/caddy_filter.py --profile mysite

Note: no `>>` redirect needed — output goes to `cron_log` defined in the profile.

## Logrotate

The provided `logrotate.conf` uses `copytruncate` because the filter keeps the log file open in append mode. Install it with:

    ./install.sh --with-logrotate

or manually:

    sudo cp logrotate.conf /etc/logrotate.d/log-reader
    # Then replace @INSTALL_DIR@ and 'su USER GROUP' with real values.
    sudo logrotate -d /etc/logrotate.d/log-reader

## Architecture

    /opt/log_reader/
    ├── caddy_filter.py           universal engine
    ├── stats.py                  statistics viewer
    ├── bots_view.py              bots log viewer
    ├── reset.sh                  per-profile reset
    ├── backup.sh                 tar --zstd backup
    ├── help.txt                  short reference (aliases)
    ├── debug.txt                 troubleshooting reference
    ├── logrotate.conf            rotation config template
    │
    ├── profiles/                 profile configs
    │   └── example.toml
    │
    ├── log/                      runtime logs
    │   ├── <profile>_dl.log
    │   ├── <profile>_cron.log
    │   └── bots_<site>.log
    │
    ├── state/                    per-profile state
    │   ├── <profile>_filter_offset
    │   ├── <profile>_filter_stats.json
    │   └── <profile>_bots_cache.json
    │
    ├── backup/                   tar.zst archives
    │
    └── GeoLite2-City.mmdb        shared geo database

## How it works

1. **Read** new lines from the Caddy access log starting at the last known offset.
2. **Parse** each line as JSON. Extract `request.uri`, `request.remote_ip`, `request.headers.User-Agent`, `duration`, `size`, `status`, `ts`.
3. **Filter** by keywords (substring match on URI).
4. **Skip** IPs from the ignore list.
5. **Verify bots** if the profile has `bots_log`:
   - `is-crawler` checks User-Agent against a database of ~1200 patterns.
   - If it's a crawler, forward-confirmed reverse DNS validates the IP.
   - Verified → written to `bots.log`.
6. **Enrich** the record: country from GeoIP, human-readable duration, speed, transformed filename, UA tag.
7. **Format** using `output_format` with placeholder substitution.
8. **Write** to the output log (optionally numbered) and update cumulative statistics.
9. **Persist** offset, stats, and bots cache.

## GeoLite2 auto-update

The GeoLite2 database is downloaded on first run and refreshed every 30 days. The URL is hardcoded in `caddy_filter.py`:

    GEOLITE_DB_URL = "https://cdn.jsdelivr.net/npm/geolite2-city/GeoLite2-City.mmdb.gz"

If the download fails but an existing database is valid, the filter continues with the old one and logs a warning.

To force a refresh:

    rm /opt/log_reader/GeoLite2-City.mmdb
    python3 /opt/log_reader/caddy_filter.py --profile mysite

## Verified bots

Detection uses two steps:

1. **User-Agent match** via [is-crawler](https://pypi.org/project/is-crawler/).
2. **Forward-confirmed reverse DNS** (FCrDNS):
   - Reverse DNS on the IP → hostname.
   - Forward DNS on the hostname → must resolve back to the same IP.
   - Hostname must end with a trusted suffix (`.googlebot.com`, `.yandex.ru`, etc.).

This prevents User-Agent spoofing: even if someone sets `User-Agent: Googlebot`, they cannot control the reverse DNS of their IP.

Trusted suffixes are configured in `caddy_filter.py`:

    TRUSTED_RDNS_SUFFIXES = (
        ".googlebot.com", ".google.com",
        ".search.msn.com",
        ".yandex.ru", ".yandex.net", ".yandex.com",
        ".applebot.apple.com", ".apple.com",
        ".duckduckgo.com",
    )

## Troubleshooting

See `debug.txt` for a comprehensive list of diagnostic commands.

Common issues:

- **`ModuleNotFoundError: tomli`** — install with `pip3 install --user tomli` (Python < 3.11 only).
- **`Permission denied` on Caddy log** — grant ACL: `sudo setfacl -m u:$USER:r-- /var/log/caddy/access.log`.
- **Empty output log** — check that keywords match URIs in the log:
      grep 'your-keyword' /var/log/caddy/access.log | head
- **Logrotate complains about "insecure permissions"** — ensure `su USER GROUP` in `/etc/logrotate.d/log-reader` matches the actual owner of the install directory.

## Contributing

Bug reports and pull requests welcome at [github.com/gpcore-team/log-reader](https://github.com/gpcore-team/log-reader).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
