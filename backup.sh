#!/bin/bash
# /opt/log_reader/backup.sh — создать бэкап GPCORE Log Reader
#
# Использование:
#   /opt/log_reader/backup.sh
#
# Что делает:
#   1. Создаёт .tar.zst архив всей директории /opt/log_reader/
#      с исключением GeoLite2-City.mmdb (перекачается автоматически)
#      и самой папки backup/ (иначе рекурсия).
#   2. Выводит имя и размер нового архива, а также общее количество.
#
# Автоудаление старых архивов отключено намеренно — управление
# вручную до внедрения системы резервного копирования на хосте.

set -e

BASE=/opt/log_reader
BKDIR="$BASE/backup"

# Сколько архивов хранить (используется только если включите автоочистку)
# KEEP=30

mkdir -p "$BKDIR"

ARCHIVE="$BKDIR/log_reader_$(date +%F).tar.zst"

tar --zstd \
    --exclude='GeoLite2-City.mmdb' \
    --exclude='opt/log_reader/backup' \
    --exclude='opt/log_reader/__pycache__' \
    -cf "$ARCHIVE" \
    -C / opt/log_reader/
	
# --exclude='GeoLite2-City.mmdb' - исключения для архивирования	

# ─── Удаление устаревших архивов (отключено) ─────────────────────────
# Раскомментируйте блок, если нужно оставить только $KEEP последних.
#
# find "$BKDIR" -maxdepth 1 -name 'log_reader_*.tar.zst' -printf '%T@ %p\n' \
#   | sort -rn \
#   | tail -n +$((KEEP+1)) \
#   | cut -d' ' -f2- \
#   | xargs -r rm
# ─────────────────────────────────────────────────────────────────────

# Статистика по свежему архиву
if [ ! -f "$ARCHIVE" ]; then
    echo "[backup] ОШИБКА: архив не создан" >&2
    exit 1
fi

SIZE=$(du -h "$ARCHIVE" | cut -f1)
COUNT=$(find "$BKDIR" -maxdepth 1 -name 'log_reader_*.tar.zst' | wc -l)

echo "[backup] Создан: $(basename "$ARCHIVE") (${SIZE})"
echo "[backup] Всего архивов: ${COUNT}"