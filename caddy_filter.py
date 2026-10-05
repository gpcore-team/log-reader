#!/usr/bin/env python3
"""
GPCORE Log Reader — universal Caddy access log filter.

Читает JSON-лог Caddy инкрементально, фильтрует записи по ключевым словам
из профиля, обогащает их (страна, время, скорость, имя файла) и пишет
результат в отдельный лог. Профили описаны в profiles/*.toml.

Использование:
    caddy_filter.py --profile NAME
    caddy_filter.py --list
    caddy_filter.py --help

Без аргументов выводится справка и список доступных профилей.
"""

import argparse
import gzip
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

try:
    # Python 3.11+
    import tomllib
except ImportError:
    try:
        # Python 3.8–3.10
        import tomli as tomllib
    except ImportError:
        print(
            "[ERROR] TOML parser not available.\n"
            "  • Python 3.11+ has tomllib built-in.\n"
            "  • Python 3.8–3.10: pip install tomli",
            file=sys.stderr,
        )
        sys.exit(2)

# Форсируем UTF-8 для stdout/stderr — cron запускает Python с LANG=C.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────────────────────────────────────
#  ПУТИ И ОБЩИЕ КОНСТАНТЫ
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR      = "/opt/log_reader"
PROFILES_DIR  = os.path.join(BASE_DIR, "profiles")

# Кэш geo-базы
GEOLITE_DB_URL       = "https://cdn.jsdelivr.net/npm/geolite2-city/GeoLite2-City.mmdb.gz"
GEOLITE_MIN_SIZE     = 1_000_000
GEOLITE_DL_TIMEOUT   = 60
GEOLITE_MAX_AGE_DAYS = 30

# FCrDNS-суффиксы для верификации ботов
TRUSTED_RDNS_SUFFIXES = (
    ".googlebot.com",
    ".google.com",
    ".search.msn.com",
    ".yandex.ru",
    ".yandex.net",
    ".yandex.com",
    ".applebot.apple.com",
    ".apple.com",
    ".duckduckgo.com",
)
BOTS_CACHE_TTL = 86400

def _abs_path(p):
    """Развернуть относительный путь в абсолютный внутри BASE_DIR."""
    if not p:
        return p
    if os.path.isabs(p):
        return p
    return os.path.join(BASE_DIR, p)

# ──────────────────────────────────────────────────────────────────────────────
#  РЕГУЛЯРКИ
# ──────────────────────────────────────────────────────────────────────────────

_UA_GP_UPDATER_RE  = re.compile(r"GP_Updater_favourites/v([\d.]+)", re.IGNORECASE)
_UA_POWERSHELL_RE  = re.compile(r"PowerShell", re.IGNORECASE)

# ──────────────────────────────────────────────────────────────────────────────
#  ГЕОЛОКАЦИЯ
# ──────────────────────────────────────────────────────────────────────────────


def _download_geolite(dest_path):
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    print(f"[GEOIP] Downloading {GEOLITE_DB_URL} ...", file=sys.stderr)

    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".mmdb.gz", delete=False, dir=dest_dir
    ) as tmp_gz:
        tmp_gz_path = tmp_gz.name
        try:
            req = urllib.request.Request(
                GEOLITE_DB_URL, headers={"User-Agent": "gpcore-logfilter/2.0"}
            )
            with urllib.request.urlopen(req, timeout=GEOLITE_DL_TIMEOUT) as resp:
                shutil.copyfileobj(resp, tmp_gz, length=1024 * 64)
        except Exception as exc:
            os.unlink(tmp_gz_path)
            raise RuntimeError(f"Cannot download GeoLite2: {exc}") from exc

    tmp_mmdb_fd, tmp_mmdb_path = tempfile.mkstemp(suffix=".mmdb", dir=dest_dir)
    os.close(tmp_mmdb_fd)
    try:
        with gzip.open(tmp_gz_path, "rb") as f_in, open(tmp_mmdb_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
        if os.path.getsize(tmp_mmdb_path) < GEOLITE_MIN_SIZE:
            raise RuntimeError("Unpacked GeoLite2 file too small — corrupted?")
        os.replace(tmp_mmdb_path, dest_path)
    finally:
        if os.path.exists(tmp_gz_path):
            os.unlink(tmp_gz_path)
        if os.path.exists(tmp_mmdb_path):
            os.unlink(tmp_mmdb_path)

    print(
        f"[GEOIP] Installed: {dest_path} "
        f"({os.path.getsize(dest_path) / 1024 / 1024:.1f} MB)",
        file=sys.stderr,
    )


def _ensure_geolite(db_path):
    needs_download = False
    reason = ""
    if not os.path.isfile(db_path):
        needs_download = True
        reason = "missing"
    elif os.path.getsize(db_path) < GEOLITE_MIN_SIZE:
        needs_download = True
        reason = "corrupted"
    else:
        age_days = (time.time() - os.path.getmtime(db_path)) / 86400
        if age_days > GEOLITE_MAX_AGE_DAYS:
            needs_download = True
            reason = f"stale ({age_days:.0f} d)"

    if not needs_download:
        return

    print(f"[GEOIP] Database {reason}, updating...", file=sys.stderr)
    try:
        _download_geolite(db_path)
    except Exception as exc:
        if os.path.isfile(db_path) and os.path.getsize(db_path) >= GEOLITE_MIN_SIZE:
            print(
                f"[GEOIP] Update failed ({exc}). Using current database.",
                file=sys.stderr,
            )
            return
        raise


# ──────────────────────────────────────────────────────────────────────────────
#  FCrDNS — DETECTION OF VERIFIED BOTS
# ──────────────────────────────────────────────────────────────────────────────


def _fallback_forward_confirmed_rdns(ip, suffixes):
    try:
        socket.setdefaulttimeout(3)
        hostname = socket.gethostbyaddr(ip)[0]
        if not hostname.endswith(suffixes):
            return None
        if socket.gethostbyname(hostname) == ip:
            return hostname
        return None
    except (socket.herror, socket.gaierror, OSError):
        return None


try:
    from is_crawler import is_crawler, crawler_name
    try:
        from is_crawler.ip import forward_confirmed_rdns
    except ImportError:
        forward_confirmed_rdns = _fallback_forward_confirmed_rdns
except ImportError:
    def is_crawler(ua):
        return False
    def crawler_name(ua):
        return ""
    forward_confirmed_rdns = _fallback_forward_confirmed_rdns


# ──────────────────────────────────────────────────────────────────────────────
#  PROFILE LOADING
# ──────────────────────────────────────────────────────────────────────────────


class ProfileError(Exception):
    pass


def list_profiles():
    """Return list of (profile_name, display_name, site, description)."""
    result = []
    if not os.path.isdir(PROFILES_DIR):
        return result
    for fname in sorted(os.listdir(PROFILES_DIR)):
        if not fname.endswith(".toml"):
            continue
        name = fname[:-5]
        path = os.path.join(PROFILES_DIR, fname)
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            prof = data.get("profile", {})
            result.append((
                name,
                prof.get("name", name),
                prof.get("site", ""),
                prof.get("description", ""),
            ))
        except Exception:
            result.append((name, name, "", "(cannot parse)"))
    return result


def _expand_site(template, site):
    """Replace {site} placeholder in a path template."""
    if template is None:
        return None
    return template.replace("{site}", site or "")


def load_profile(profile_name):
    path = os.path.join(PROFILES_DIR, profile_name + ".toml")
    if not os.path.isfile(path):
        raise ProfileError(f"Profile not found: {path}")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise ProfileError(f"Cannot parse {path}: {exc}") from exc

    prof = data.get("profile")
    if not prof:
        raise ProfileError(f"{path}: missing [profile] section")

    required = ("input_log", "keywords", "output_log")
    for key in required:
        if key not in prof:
            raise ProfileError(f"{path}: missing required field 'profile.{key}'")

    site = prof.get("site", "")

    def _resolved(key, default=None):
        """Читает поле, разворачивает {site} и делает путь абсолютным."""
        val = prof.get(key, default)
        if not isinstance(val, str):
            return val
        val = val.replace("{site}", site or "")
        if not val:
            return val
        return _abs_path(val)

    cfg = {
        "name":          prof.get("name", profile_name),
        "description":   prof.get("description", ""),
        "site":          site,
        "input_log":     prof["input_log"],
        "keywords":      list(prof["keywords"]),
        "ignore_ips":    set(prof.get("ignore_ips", [])),
        "output_log":    _resolved("output_log"),
        "offset_file":   _resolved("offset_file"),
        "stats_file":    _resolved("stats_file"),
        "bots_log":      _resolved("bots_log", ""),
        "bots_cache":    _resolved("bots_cache", ""),
        "cron_log":      _resolved("cron_log", ""),
        "number_lines":  bool(prof.get("number_lines", False)),
        "output_format": prof.get("output_format", "{ts}  {country}  {ip}  {filename}"),
        "geolite_db":    _resolved("geolite_db",
                                   os.path.join(BASE_DIR, "GeoLite2-City.mmdb")),
    }

    if not cfg["output_log"]:
        raise ProfileError(f"{path}: output_log is empty")

    if not cfg["offset_file"]:
        cfg["offset_file"] = os.path.join(BASE_DIR, f".{profile_name}_filter_offset")
    if not cfg["stats_file"]:
        cfg["stats_file"] = os.path.join(BASE_DIR, f".{profile_name}_filter_stats.json")

    if cfg["bots_log"] and not cfg["bots_cache"]:
        cfg["bots_cache"] = os.path.join(BASE_DIR, f".{profile_name}_bots_cache.json")

    ft = data.get("filename_transform") or {}
    cfg["filename_transform"] = {
        "mode":    ft.get("mode", "full"),
        "pattern": ft.get("pattern", ""),
        "result":  ft.get("result", ""),
    }

    cfg["profile_name"] = profile_name
    cfg["config_path"]  = path
    return cfg


# ──────────────────────────────────────────────────────────────────────────────
#  FORMATTING HELPERS
# ──────────────────────────────────────────────────────────────────────────────


def fmt_duration(seconds):
    """Human-readable duration: 45s / 2m 40s / 1h 2m 5s."""
    if seconds is None or seconds < 0:
        return "?"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def fmt_speed(size_bytes, duration_sec):
    """Speed in MB/s with two decimals."""
    if not duration_sec or duration_sec <= 0:
        return "—"
    if size_bytes is None or size_bytes <= 0:
        return "—"
    mbps = size_bytes / duration_sec / 1024 / 1024
    return f"{mbps:.2f} MB/s"


def fmt_country(code):
    return code if code else "??"


# ──────────────────────────────────────────────────────────────────────────────
#  FILENAME TRANSFORM
# ──────────────────────────────────────────────────────────────────────────────


def transform_filename(basename, cfg):
    ft = cfg.get("filename_transform") or {}
    if ft.get("mode") != "short":
        return basename
    pattern = ft.get("pattern")
    result  = ft.get("result")
    if not pattern or not result:
        return basename

    try:
        rx = re.compile(pattern)
    except re.error as exc:
        print(f"[transform] regex compile error: {exc}", file=sys.stderr)
        return basename

    m = rx.search(basename)
    if not m:
        return basename

    # Подставляем группы по номерам {1}, {2}, ... с сохранением явных ошибок.
    result_str = result
    for idx, val in enumerate(m.groups(), start=1):
        result_str = result_str.replace(f"{{{idx}}}", val if val is not None else "")

    return result_str


# ──────────────────────────────────────────────────────────────────────────────
#  UA CLASSIFICATION
# ──────────────────────────────────────────────────────────────────────────────


def classify_ua_for_gpfav(ua_string):
    """Legacy classification — only meaningful for the gpfav profile.
    Returns UPD{v} / PSH / '' (empty)."""
    if not ua_string:
        return ""
    m = _UA_GP_UPDATER_RE.search(ua_string)
    if m:
        return f"UPD{m.group(1)}"
    if _UA_POWERSHELL_RE.search(ua_string):
        return "PSH"
    return ""


# ──────────────────────────────────────────────────────────────────────────────
#  STATE (offset / stats / bots cache)
# ──────────────────────────────────────────────────────────────────────────────


def load_offset(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            return 0
    return 0


def save_offset(path, offset):
    with open(path, "w") as f:
        f.write(str(offset))


def _empty_stats():
    return {"lineno": 0, "total": 0, "by_tag": {}, "by_file": {},
            "by_country": {}, "bots": 0}


def load_stats(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, default in (("lineno", 0), ("total", 0), ("bots", 0),
                                 ("by_tag", {}), ("by_file", {}), ("by_country", {})):
                data.setdefault(key, default)
            return data
        except Exception:
            pass
    return _empty_stats()


def save_stats(path, stats):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def load_bots_cache(path):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_bots_cache(path, cache):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# ──────────────────────────────────────────────────────────────────────────────
#  MIGRATION — восстановление статистики из существующего output_log
# ──────────────────────────────────────────────────────────────────────────────

# Разбор строки лога. Захватывает:
#   1 — номер строки (опционально, если number_lines)
#   2 — timestamp
#   3 — страна
#   4 — IP
#   5 — ua_tag (опционально)
#   6 — filename
_LOG_LINE_RE = re.compile(
    r"^\s*(?:(\d+)\s+)?"
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(\S+)\s+"
    r"(\S+)\s+"
    r"(?:\[([^\]]+)\]\s+)?"
    r"(\S+)\s*$"
)


def migrate_log_if_needed(cfg, stats):
    """
    Если stats пусты, а output_log уже содержит записи — восстановить
    total, by_tag, by_file, by_country и lineno из output_log.
    Возвращает актуальное количество строк в output_log.
    """
    path = cfg["output_log"]
    if not os.path.exists(path):
        return 0

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return 0

    # Если number_lines включён — lineno должен соответствовать
    # количеству строк, даже если stats уже частично заполнены.
    if cfg["number_lines"]:
        max_lineno = stats.get("lineno", 0)
        if max_lineno < len(lines):
            stats["lineno"] = len(lines)

    # Если счётчики пусты — пересчитать всё из лога.
    if stats.get("total", 0) == 0:
        stats["by_tag"] = {}
        stats["by_file"] = {}
        stats["by_country"] = {}
        stats["total"] = 0

        for raw in lines:
            m = _LOG_LINE_RE.match(raw.rstrip("\n"))
            if not m:
                continue
            country  = m.group(3)
            tag      = m.group(5) or "browser"
            filename = m.group(6)

            stats["by_tag"][tag]         = stats["by_tag"].get(tag, 0) + 1
            stats["by_file"][filename]   = stats["by_file"].get(filename, 0) + 1
            stats["by_country"][country] = stats["by_country"].get(country, 0) + 1
            stats["total"] += 1

    return len(lines)

# ──────────────────────────────────────────────────────────────────────────────
#  BOT VERIFICATION
# ──────────────────────────────────────────────────────────────────────────────


def verify_trusted_bot(ip, ua, cache):
    now = time.time()
    if ip in cache:
        entry = cache[ip]
        if now - entry.get("ts", 0) < BOTS_CACHE_TTL:
            return entry.get("bot", False), entry.get("name", "")

    if not is_crawler(ua):
        cache[ip] = {"bot": False, "name": "", "ts": now}
        return False, ""

    name = crawler_name(ua) or ""
    hostname = forward_confirmed_rdns(ip, TRUSTED_RDNS_SUFFIXES)
    is_bot = bool(hostname)
    cache[ip] = {"bot": is_bot, "name": name, "ts": now}
    return is_bot, name


# ──────────────────────────────────────────────────────────────────────────────
#  LOG READING
# ──────────────────────────────────────────────────────────────────────────────


def read_new_lines(filepath, offset):
    with open(filepath, "rb") as f:
        f.seek(offset)
        for raw_line in f:
            offset = f.tell()
            try:
                yield offset, raw_line.decode("utf-8", errors="replace").strip()
            except Exception:
                continue


# ──────────────────────────────────────────────────────────────────────────────
#  RECORD PROCESSING
# ──────────────────────────────────────────────────────────────────────────────


def process_line(line, cfg, bots_cache, geo_reader):
    """
    Return one of:
      ("download", formatted_line, tag_key, filename, country)
      ("bot", formatted_bot_line, bot_name)
      None — skip
    """
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    request = data.get("request") or {}
    uri = request.get("uri", "")

    if not any(kw in uri for kw in cfg["keywords"]):
        return None

    remote_ip = request.get("remote_ip") or request.get("client_ip") or "?"

    if remote_ip in cfg["ignore_ips"]:
        return None

    headers = request.get("headers") or {}
    ua_raw = headers.get("User-Agent") or headers.get("user-agent") or []
    if isinstance(ua_raw, list):
        ua = ua_raw[0] if ua_raw else ""
    else:
        ua = ua_raw or ""

    # Geo
    try:
        country = geo_reader.city(remote_ip).country.iso_code or "??"
    except Exception:
        country = "??"

    # Bot check (only if the profile has bots_log)
    if cfg["bots_log"]:
        is_bot, bot_name = verify_trusted_bot(remote_ip, ua, bots_cache)
        if is_bot:
            body = f"{_ts_str(data)}  {country:>3}  {remote_ip:<16} {bot_name:<20} {uri}"
            return ("bot", body, bot_name)

    # Duration / size / speed
    duration = data.get("duration")
    size     = data.get("size")

    # Timestamp
    ts_str = _ts_str(data)

    # Filename transform
    basename = os.path.basename(uri.split("?")[0])
    filename = transform_filename(basename, cfg)

    # UA tag (legacy, useful for gpfav)
    ua_tag = classify_ua_for_gpfav(ua)

    # Format line
    placeholders = {
        "ts":       ts_str,
        "country":  fmt_country(country),
        "ip":       remote_ip,
        "duration": fmt_duration(duration),
        "speed":    fmt_speed(size, duration),
        "filename": filename,
        "uri":      uri,
        "size":     size if size is not None else "?",
        "status":   data.get("status", "?"),
        "ua_tag":   ua_tag,
    }

    try:
        body = cfg["output_format"].format(**placeholders)
    except KeyError as exc:
        raise ProfileError(
            f"Unknown placeholder in output_format: {exc}. "
            f"Available: {sorted(placeholders.keys())}"
        )

    tag_key = ua_tag if ua_tag else "browser"
    return ("download", body, tag_key, filename, country)


def _ts_str(data):
    ts_raw = data.get("ts")
    if ts_raw:
        return datetime.fromtimestamp(ts_raw).strftime("%Y-%m-%d %H:%M:%S")
    return "?"


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────────────


def run_profile(profile_name):
    cfg = load_profile(profile_name)

    if not os.path.isfile(cfg["input_log"]):
        print(f"[ERROR] Input log not found: {cfg['input_log']}", file=sys.stderr)
        sys.exit(1)

    # Redirect stdout/stderr to cron_log if configured
    if cfg["cron_log"]:
        cron_f = open(cfg["cron_log"], "a", encoding="utf-8")
        sys.stdout = cron_f
        sys.stderr = cron_f
        print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
              f"[{profile_name}] ---")

    # GeoLite2
    _ensure_geolite(cfg["geolite_db"])
    import geoip2.database
    geo_reader = geoip2.database.Reader(cfg["geolite_db"])

    offset     = load_offset(cfg["offset_file"])
    stats      = load_stats(cfg["stats_file"])
    bots_cache = load_bots_cache(cfg["bots_cache"])

    # Восстановление статистики из лога (если stats пуст)
    migrate_log_if_needed(cfg, stats)

    # Шапка с абсолютными путями
    print(f"[PROFILE] {cfg['name']} ({profile_name})")
    if cfg["description"]:
        print(f"[PROFILE] {cfg['description']}")
    print(f"[PROFILE] input:  {cfg['input_log']}")
    print(f"[PROFILE] output: {cfg['output_log']}")
    if cfg["bots_log"]:
        print(f"[PROFILE] bots:   {cfg['bots_log']}")

    processed    = 0
    matched      = 0
    bots_found   = 0

    open_files = [open(cfg["output_log"], "a", encoding="utf-8")]
    if cfg["bots_log"]:
        open_files.append(open(cfg["bots_log"], "a", encoding="utf-8"))
    out_f  = open_files[0]
    bots_f = open_files[1] if len(open_files) > 1 else None

    try:
        for new_offset, line in read_new_lines(cfg["input_log"], offset):
            processed += 1
            try:
                result = process_line(line, cfg, bots_cache, geo_reader)
            except ProfileError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                sys.exit(1)

            if result is None:
                offset = new_offset
                continue

            if result[0] == "bot":
                _, body, _ = result
                bots_f.write(body + "\n")
                bots_f.flush()
                stats["bots"] += 1
                bots_found += 1
            else:
                _, body, tag_key, filename, country = result
                if cfg["number_lines"]:
                    stats["lineno"] += 1
                    line_out = f"{stats['lineno']:>5}  {body}"
                else:
                    line_out = body

                stats["total"] += 1
                stats["by_tag"][tag_key] = stats["by_tag"].get(tag_key, 0) + 1
                stats["by_file"][filename] = stats["by_file"].get(filename, 0) + 1
                stats["by_country"][country] = stats["by_country"].get(country, 0) + 1

                out_f.write(line_out + "\n")
                out_f.flush()
                matched += 1

            offset = new_offset
    finally:
        for f in open_files:
            f.close()

    save_offset(cfg["offset_file"], offset)
    save_stats(cfg["stats_file"], stats)
    if cfg["bots_log"]:
        save_bots_cache(cfg["bots_cache"], bots_cache)

    print(f"[OK] Processed: {processed} new, {matched} downloads, "
          f"{bots_found} bots, offset={offset}")
    parts = " ".join(f"{k}={v}" for k, v in sorted(stats["by_tag"].items()))
    print(f"[STATS] Total: {stats['total']}  |  {parts}  |  bots_total={stats['bots']}")


# ──────────────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────────────


def print_help():
    profiles = list_profiles()

    print("GPCORE Log Reader — universal Caddy access log filter")
    print()
    print("Usage:")
    print("    caddy_filter.py --profile NAME")
    print("    caddy_filter.py --list")
    print("    caddy_filter.py --help")
    print()
    if profiles:
        print("Available profiles:")
        width = max(len(p[0]) for p in profiles) + 2
        for name, disp, site, _desc in profiles:
            site_part = f"({site})" if site else ""
            print(f"    {name:<{width}} {disp:<24} {site_part}")
    else:
        print("No profiles found in " + PROFILES_DIR)
    print()
    print("Without arguments a summary is printed.")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="caddy_filter.py",
        description="GPCORE Log Reader — universal Caddy access log filter",
        add_help=False,
    )
    parser.add_argument("--profile", "-p", metavar="NAME",
                        help="Profile name (see --list)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available profiles and exit")
    parser.add_argument("--help", "-h", action="store_true",
                        help="Show this help and exit")

    args = parser.parse_args()

    if args.help or (not args.profile and not args.list):
        print_help()
        sys.exit(0)

    if args.list:
        print_help()
        sys.exit(0)

    try:
        run_profile(args.profile)
    except ProfileError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("[INTERRUPTED]", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()