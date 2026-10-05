#!/usr/bin/env python3
"""
Показать хвост лога ботов и общее количество записей.

Использование:
    bots_view.py PATH [N]
    bots_view.py /opt/log_reader/bots_gpcore.ru.log
    bots_view.py /opt/log_reader/bots_gpcore.ru.log 100
    bots_view.py /opt/log_reader/bots_gpcore.ru.log 0     # только счётчик

Если PATH не задан — используется bots.log (обратная совместимость).
"""

import sys
import os

DEFAULT_PATH = "/opt/log_reader/bots.log"
DEFAULT_N    = 50


def main():
    if len(sys.argv) >= 2:
        log_path = sys.argv[1]
    else:
        log_path = DEFAULT_PATH

    n = DEFAULT_N
    if len(sys.argv) >= 3:
        try:
            n = int(sys.argv[2])
        except ValueError:
            print(f"Ожидалось число, получено: {sys.argv[2]!r}", file=sys.stderr)
            sys.exit(2)

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Лог не найден: {log_path}")
        sys.exit(1)

    total = len(lines)

    if n > 0 and total > 0:
        tail = lines[-n:] if n < total else lines
        for line in tail:
            print(line.rstrip("\n"))
        print()

    print(f"Файл: {log_path}")
    print(f"Всего записей от ботов: {total}")
    if n > 0 and total > n:
        print(f"Показаны последние {n}.")


if __name__ == "__main__":
    main()