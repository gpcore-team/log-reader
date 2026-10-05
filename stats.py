#!/usr/bin/env python3
"""
Универсальный просмотр статистики по логу загрузок профиля.

Использование:
    stats.py --profile NAME [--verbose]

Без аргументов — справка и список профилей.
"""

import argparse
import os
import re
import sys
from collections import Counter

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("[ERROR] TOML parser not available.", file=sys.stderr)
        sys.exit(2)

BASE_DIR     = "/opt/log_reader"
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")

# Разбор строки лога: ts, country, ip, tail.
# tail = всё, что после IP: либо "  TAG    filename", либо "  filename".
_LINE_RE = re.compile(
    r"^\s*(?:\d+\s+)?"                                          # опциональный lineno
    r"(?:\[)?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\])?\s+"  # ts
    r"(\S+)\s+"                                                 # country
    r"(\S+)\s+"                                                 # ip
    r"(.*?)\s*$"                                                # tail
)

# Известные метки, которые могут встречаться в начале tail
_TAG_RE = re.compile(r"^\[?(PSH|UPD[\d.]+|Google-Lens|Googlebot|YandexBot|Bingbot)\]?$")

_TAG_LABELS = {
    "browser": "Браузер (ручная)",
    "PSH":     "PowerShell (старый батник)",
}


# ─── Профили ────────────────────────────────────────────────────────────────


def list_profiles():
    result = []
    if not os.path.isdir(PROFILES_DIR):
        return result
    for fname in sorted(os.listdir(PROFILES_DIR)):
        if not fname.endswith(".toml"):
            continue
        name = fname[:-5]
        try:
            with open(os.path.join(PROFILES_DIR, fname), "rb") as f:
                data = tomllib.load(f)
            prof = data.get("profile", {})
            result.append((name, prof.get("name", name), prof.get("site", "")))
        except Exception:
            result.append((name, name, ""))
    return result


def load_profile(name):
    path = os.path.join(PROFILES_DIR, name + ".toml")
    if not os.path.isfile(path):
        print(f"[ERROR] Profile not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    prof = data.get("profile", {})
    out  = prof.get("output_log", "")
    if not os.path.isabs(out):
        out = os.path.join(BASE_DIR, out)
    return {"output_log": out, "name": prof.get("name", name)}


# ─── Парсинг лога ───────────────────────────────────────────────────────────


def _parse_log(path):
    by_tag     = Counter()
    by_file    = Counter()
    by_country = Counter()
    total      = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = _LINE_RE.match(line.rstrip("\n"))
            if not m:
                continue
            country = m.group(2)
            tail    = m.group(4).strip()

            tag      = "browser"
            filename = tail

            parts = tail.split(None, 1)
            if parts and _TAG_RE.match(parts[0]):
                tag = parts[0].strip("[]")
                filename = parts[1].strip() if len(parts) > 1 else ""

            by_tag[tag] += 1
            by_file[filename] += 1
            by_country[country] += 1
            total += 1

    return total, by_tag, by_file, by_country


# ─── Форматирование таблиц ──────────────────────────────────────────────────


def _print_table(title, rows, col1_header, col2_header="Count", min_width1=0):
    """
    rows: список кортежей (col1_value, col2_value).
    min_width1: минимальная ширина первой колонки (для единой геометрии).
    """
    if not rows:
        return

    width1 = max(len(str(r[0])) for r in rows)
    width1 = max(width1, len(col1_header), min_width1)

    # Отступ слева 2 пробела, потом колонка, потом " | " и вторая колонка
    line_fmt = "  {:<" + str(width1) + "} | {:>6}"
    sep_line = "  " + "-" * width1 + "-+-------"
    head_fmt = "  {:<" + str(width1) + "} | {:>6}"

    print(title + ":")
    print(head_fmt.format(col1_header, col2_header))
    print(sep_line)
    for a, b in rows:
        print(line_fmt.format(str(a), b))
    print()


# ─── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        prog="stats.py",
        description="Универсальная статистика по логу загрузок профиля",
        add_help=False,
    )
    parser.add_argument("--profile", "-p", metavar="NAME")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args()

    if args.help or not args.profile:
        print("Usage: stats.py --profile NAME [--verbose]")
        print()
        print("Available profiles:")
        for name, disp, site in list_profiles():
            site_part = f"({site})" if site else ""
            print(f"    {name:<12} {disp:<24} {site_part}")
        sys.exit(0)

    cfg = load_profile(args.profile)
    path = cfg["output_log"]

    try:
        total, by_tag, by_file, by_country = _parse_log(path)
    except FileNotFoundError:
        print(f"Лог не найден: {path}")
        sys.exit(1)

    print(f"Профиль: {cfg['name']} ({args.profile})")
    print(f"Лог:     {path}")
    print(f"Всего загрузок: {total}")
    print()

    # По способу
    tag_rows = []
    for tag, count in sorted(by_tag.items(), key=lambda x: -x[1]):
        tag_rows.append((_TAG_LABELS.get(tag, tag), count))
    _print_table("По способу загрузки", tag_rows, "Способ", min_width1=32)

    # По файлу
    file_rows = [(name, count) for name, count in
                 sorted(by_file.items(), key=lambda x: -x[1])]
    _print_table("По файлу", file_rows, "Файл", min_width1=32)

    # По стране
    limit = 20 if args.verbose else 10
    country_rows = [(cc, count) for cc, count in by_country.most_common(limit)]
    _print_table(f"Топ-{limit} стран", country_rows, "Страна", min_width1=32)


if __name__ == "__main__":
    main()