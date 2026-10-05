#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  GPCORE Log Reader — installer
#
#  Usage:
#      ./install.sh                          interactive (default)
#      ./install.sh --install-dir /opt/...   custom directory
#      ./install.sh --profile NAME --with-cron
#      ./install.sh --skip-deps
#      ./install.sh --help
#
#  What it does:
#      1. Checks Python version (3.8+).
#      2. Installs Python dependencies (tomli, geoip2, is-crawler).
#      3. Creates install directory with log/, state/, backup/, profiles/.
#      4. Copies scripts from the current directory.
#      5. Optionally installs logrotate config (requires sudo).
#      6. Optionally installs cron entry (requires --profile).
#
#  Idempotent: safe to re-run.
# ═══════════════════════════════════════════════════════════════════════════

set -e

# ─── Defaults ───────────────────────────────────────────────────────────────

INSTALL_DIR="/opt/log_reader"
PROFILE=""
WITH_CRON=0
WITH_LOGROTATE=0
SKIP_DEPS=0
INTERACTIVE=1

# ─── Colors ─────────────────────────────────────────────────────────────────

if [ -t 1 ]; then
    C_OK="\033[32m"
    C_WARN="\033[33m"
    C_ERR="\033[31m"
    C_HEAD="\033[1;36m"
    C_OFF="\033[0m"
else
    C_OK=""
    C_WARN=""
    C_ERR=""
    C_HEAD=""
    C_OFF=""
fi

msg()  { echo -e "$*"; }
ok()   { echo -e "${C_OK}[OK]${C_OFF} $*"; }
warn() { echo -e "${C_WARN}[WARN]${C_OFF} $*"; }
err()  { echo -e "${C_ERR}[ERROR]${C_OFF} $*" >&2; }
head() { echo; echo -e "${C_HEAD}═══ $* ═══${C_OFF}"; }

# ─── Argparse ───────────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir)
            INSTALL_DIR="$2"; shift 2; INTERACTIVE=0 ;;
        --profile)
            PROFILE="$2"; shift 2 ;;
        --with-cron)
            WITH_CRON=1; shift ;;
        --with-logrotate)
            WITH_LOGROTATE=1; shift ;;
        --skip-deps)
            SKIP_DEPS=1; shift ;;
        --non-interactive)
            INTERACTIVE=0; shift ;;
        --help|-h)
            sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            err "Unknown argument: $1"
            echo "Run with --help for usage."
            exit 2 ;;
    esac
done

# ─── Interactive mode ───────────────────────────────────────────────────────

if [ "$INTERACTIVE" -eq 1 ] && [ -t 0 ]; then
    head "GPCORE Log Reader — installer"
    echo
    echo "Interactive mode. Press Enter to accept defaults."
    echo

    read -r -p "Install directory [$INSTALL_DIR]: " ans
    [ -n "$ans" ] && INSTALL_DIR="$ans"

    read -r -p "Profile name for cron (leave empty to skip): " ans
    [ -n "$ans" ] && PROFILE="$ans"

    if [ -n "$PROFILE" ]; then
        read -r -p "Add cron entry (every 5 min)? [y/N]: " ans
        case "$ans" in [Yy]*) WITH_CRON=1 ;; esac
    fi

    read -r -p "Install logrotate config? [y/N]: " ans
    case "$ans" in [Yy]*) WITH_LOGROTATE=1 ;; esac

    echo
    echo "Install directory: $INSTALL_DIR"
    echo "Profile for cron:  ${PROFILE:-<none>}"
    echo "Install cron:      $([ $WITH_CRON -eq 1 ] && echo yes || echo no)"
    echo "Install logrotate: $([ $WITH_LOGROTATE -eq 1 ] && echo yes || echo no)"
    echo
    read -r -p "Continue? [Y/n]: " ans
    case "$ans" in [Nn]*) echo "Aborted."; exit 0 ;; esac
fi

# ─── Check source files ─────────────────────────────────────────────────────

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

REQUIRED_FILES=(
    caddy_filter.py
    stats.py
    bots_view.py
    reset.sh
    backup.sh
    help.txt
    debug.txt
    logrotate.conf
    profiles/example.toml
)

head "Checking source files"
MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$SRC_DIR/$f" ]; then
        err "Missing: $f"
        MISSING=1
    fi
done
if [ $MISSING -eq 1 ]; then
    err "Install aborted — not all source files present."
    exit 1
fi
ok "All source files present."

# ─── Check Python ───────────────────────────────────────────────────────────

head "Checking Python"

if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found in PATH. Install python3 and retry."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    err "Python 3.8+ required, found $PY_VER"
    exit 1
fi
ok "Python $PY_VER"

# ─── Install dependencies ───────────────────────────────────────────────────

if [ "$SKIP_DEPS" -eq 0 ]; then
    head "Installing Python dependencies"

    # tomli needed for Python <3.11
    if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; then
        if python3 -c "import tomli" 2>/dev/null; then
            ok "tomli already installed"
        else
            msg "Installing tomli..."
            pip3 install --user tomli || pip3 install --break-system-packages tomli || {
                err "Failed to install tomli. Install manually: pip3 install --user tomli"
                exit 1
            }
        fi
    else
        ok "Python 3.11+ — builtin tomllib is used"
    fi

    for pkg in geoip2 is-crawler; do
        mod=$(echo "$pkg" | tr '-' '_')
        if python3 -c "import $mod" 2>/dev/null; then
            ok "$pkg already installed"
        else
            msg "Installing $pkg..."
            pip3 install --user "$pkg" || pip3 install --break-system-packages "$pkg" || {
                warn "Failed to install $pkg. Install manually: pip3 install --user $pkg"
            }
        fi
    done
else
    warn "Skipped dependency installation (--skip-deps)"
fi

# ─── Create install directory ───────────────────────────────────────────────

head "Creating install directory"

if [ -d "$INSTALL_DIR" ]; then
    warn "$INSTALL_DIR already exists — files will be overwritten"
else
    # Try without sudo first, then with
    mkdir -p "$INSTALL_DIR" 2>/dev/null || sudo mkdir -p "$INSTALL_DIR"
fi

# ownership
if [ ! -w "$INSTALL_DIR" ]; then
    warn "No write access to $INSTALL_DIR — trying with sudo"
    sudo chown "$USER":"$USER" "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR/log" "$INSTALL_DIR/state" "$INSTALL_DIR/backup" "$INSTALL_DIR/profiles"

ok "Created: $INSTALL_DIR/{log,state,backup,profiles}"

# ─── Copy files ─────────────────────────────────────────────────────────────

head "Copying files"

for f in caddy_filter.py stats.py bots_view.py reset.sh backup.sh help.txt debug.txt; do
    cp "$SRC_DIR/$f" "$INSTALL_DIR/$f"
    ok "  $f"
done

chmod 755 "$INSTALL_DIR/caddy_filter.py" \
          "$INSTALL_DIR/stats.py" \
          "$INSTALL_DIR/bots_view.py" \
          "$INSTALL_DIR/reset.sh" \
          "$INSTALL_DIR/backup.sh"

# Copy example profile only if no profiles exist yet
if [ -z "$(ls -A "$INSTALL_DIR/profiles" 2>/dev/null)" ]; then
    cp "$SRC_DIR/profiles/example.toml" "$INSTALL_DIR/profiles/example.toml"
    ok "  profiles/example.toml (template)"
else
    warn "  profiles/ already has files — example.toml NOT copied"
fi

# ─── Logrotate ──────────────────────────────────────────────────────────────

if [ "$WITH_LOGROTATE" -eq 1 ]; then
    head "Installing logrotate config"

    LR_SRC="$SRC_DIR/logrotate.conf"
    LR_DST="/etc/logrotate.d/log-reader"

    # Substitute install dir in template
    TMP_LR=$(mktemp)
    sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
        -e "s|su USER GROUP|su $USER $(id -gn)|" \
        "$LR_SRC" > "$TMP_LR"

    sudo cp "$TMP_LR" "$LR_DST"
    sudo chmod 644 "$LR_DST"
    rm -f "$TMP_LR"

    ok "Installed: $LR_DST"

    # Validate
    if sudo logrotate -d "$LR_DST" >/dev/null 2>&1; then
        ok "logrotate config valid"
    else
        warn "logrotate dry-run reported issues — check: sudo logrotate -d $LR_DST"
    fi
else
    warn "logrotate not installed (use --with-logrotate)"
fi

# ─── Cron ───────────────────────────────────────────────────────────────────

if [ -n "$PROFILE" ] && [ "$WITH_CRON" -eq 1 ]; then
    head "Installing cron entry"

    CRON_LINE="*/5 * * * * /usr/bin/python3 $INSTALL_DIR/caddy_filter.py --profile $PROFILE"

    # Read current crontab, remove any existing log-reader entries, add new
    TMP_CRON=$(mktemp)
    crontab -l 2>/dev/null | grep -v "caddy_filter.py" > "$TMP_CRON" || true
    echo "" >> "$TMP_CRON"
    echo "# GPCORE Log Reader — profile $PROFILE" >> "$TMP_CRON"
    echo "$CRON_LINE" >> "$TMP_CRON"
    crontab "$TMP_CRON"
    rm -f "$TMP_CRON"

    ok "Cron entry added: $CRON_LINE"
    msg "Verify with: crontab -l"
elif [ -n "$PROFILE" ]; then
    warn "Cron not installed. Enable with --with-cron"
fi

# ─── Summary ────────────────────────────────────────────────────────────────

head "Installation complete"

echo
echo "Install directory: $INSTALL_DIR"
echo "Profiles:          $INSTALL_DIR/profiles/"
if [ -z "$(ls -A "$INSTALL_DIR/profiles" 2>/dev/null | grep -v example.toml)" ]; then
    echo
    echo "Next steps:"
    echo "  1. Create a profile: cp $INSTALL_DIR/profiles/example.toml \\"
    echo "                            $INSTALL_DIR/profiles/<name>.toml"
    echo "  2. Edit $INSTALL_DIR/profiles/<name>.toml"
    echo "  3. Run: python3 $INSTALL_DIR/caddy_filter.py --profile <name>"
    echo "  4. Add cron entry for periodic runs."
else
    echo
    echo "Run a test:"
    echo "  python3 $INSTALL_DIR/caddy_filter.py --list"
fi
echo
