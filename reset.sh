#!/bin/bash
# reset.sh — сброс состояния профилей GPCORE Log Reader.
#
# Использование:
#     reset.sh --profile NAME          сброс одного профиля
#     reset.sh --profile_all           сброс всех профилей
#     reset.sh --help                  эта справка
#
# Что делает:
#     1. Удаляет offset, stats и bots_cache профиля.
#     2. Обнуляет основной лог и лог ботов (если указаны).
#     3. Прогоняет caddy_filter.py --profile NAME заново.
#
# Что НЕ трогает:
#     GeoLite2-City.mmdb, профили, cron-логи, backup/, сам скрипт.

set -e

BASE=/opt/log_reader
PROFILES_DIR="$BASE/profiles"
FILTER="$BASE/caddy_filter.py"


reset_profile() {
    local profile="$1"
    local cfg="$PROFILES_DIR/$profile.toml"

    if [ ! -f "$cfg" ]; then
        echo "[reset] Профиль не найден: $profile" >&2
        return 1
    fi

    echo "[reset] Профиль: $profile"

    # Разбираем output_log и связанные пути через python
    local paths
    paths=$(python3 - "$cfg" "$BASE" <<'PYEOF'
import os, sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib

cfg_path, base = sys.argv[1], sys.argv[2]
with open(cfg_path, "rb") as f:
    data = tomllib.load(f)
p = data.get("profile", {})
site = p.get("site", "")

def resolve(key):
    v = p.get(key, "")
    if not v:
        return ""
    v = v.replace("{site}", site)
    if not os.path.isabs(v):
        v = os.path.join(base, v)
    return v

for key in ("output_log", "bots_log", "offset_file", "stats_file", "bots_cache"):
    print(f"{key}={resolve(key)}")
PYEOF
)

    local output_log="" bots_log="" offset_file="" stats_file="" bots_cache=""
    while IFS='=' read -r k v; do
        case "$k" in
            output_log)  output_log="$v"  ;;
            bots_log)    bots_log="$v"    ;;
            offset_file) offset_file="$v" ;;
            stats_file)  stats_file="$v"  ;;
            bots_cache)  bots_cache="$v"  ;;
        esac
    done <<< "$paths"

    [ -n "$offset_file" ] && rm -f "$offset_file"  && echo "  rm $offset_file"
    [ -n "$stats_file"  ] && rm -f "$stats_file"   && echo "  rm $stats_file"
    [ -n "$bots_cache"  ] && rm -f "$bots_cache"   && echo "  rm $bots_cache"
    [ -n "$output_log"  ] && > "$output_log"       && echo "  truncate $output_log"
    [ -n "$bots_log"    ] && > "$bots_log"         && echo "  truncate $bots_log"

    echo "[reset] Пересборка..."
    python3 "$FILTER" --profile "$profile"
    echo
}


case "${1:-}" in
    --help|-h|"")
        cat <<'EOF'
reset.sh — сброс состояния профилей GPCORE Log Reader.

Использование:
    reset.sh --profile NAME    сброс одного профиля
    reset.sh --profile_all     сброс всех профилей
    reset.sh --help            эта справка

Что делает:
    1. Удаляет offset, stats и bots_cache профиля.
    2. Обнуляет основной лог и лог ботов.
    3. Прогоняет caddy_filter.py --profile NAME заново.

Что НЕ трогает:
    GeoLite2-City.mmdb, профили, cron-логи, backup/, сам скрипт.
EOF
        exit 0
        ;;
    --profile)
        if [ -z "${2:-}" ]; then
            echo "Укажите имя профиля: reset.sh --profile NAME" >&2
            exit 2
        fi
        reset_profile "$2"
        ;;
    --profile_all)
        shopt -s nullglob
        for cfg in "$PROFILES_DIR"/*.toml; do
            name=$(basename "$cfg" .toml)
            reset_profile "$name" || true
        done
        ;;
    *)
        echo "Неизвестный аргумент: $1" >&2
        echo "Использование: reset.sh --profile NAME | --profile_all | --help" >&2
        exit 2
        ;;
esac