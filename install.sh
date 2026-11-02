#!/usr/bin/env bash
# PatrolCraft installer for macOS (UniFi Protect PTZ patrol editor).
#
#   Install or upgrade:  curl -fsSL https://labs.yestechie.com/install/patrolcraft.sh | bash
#   Uninstall:           curl -fsSL https://labs.yestechie.com/install/patrolcraft.sh | bash -s -- --uninstall
#
# Run as your normal macOS user. No sudo is needed. Re-running upgrades in place and keeps
# your login, console settings and saved patrol points.
#
# Environment overrides (put them before bash, e.g. curl ... | PC_PORT=8090 bash):
#   PC_BASE_URL      where releases live (default https://labs.yestechie.com/install)
#   PC_VERSION       install this version instead of the one named in "latest"
#   PC_PORT          local port (default 8080)
#   PC_HOME          install folder (default ~/Library/Application Support/PatrolCraft)
#   PC_LABEL         LaunchAgent label (default com.yestechie.patrolcraft)
#   PC_RECONFIGURE=1 make a new PatrolCraft password on upgrade
#   PC_LOCAL_TARBALL install from a local release archive (testing only)
#   PC_NO_SERVICE=1  install files only, don't load the LaunchAgent (testing only)
#   PC_FORCE=1       skip the macOS check (testing only)
#   PC_YES=1         accept the terms without asking (unattended installs)
set -euo pipefail

TERMS_NOTICE='PatrolCraft is published for personal use only.
For business use, or for an integration built for your site,
contact us at yestechie.com/contact

By installing, you agree to the YesTechie Labs terms:
labs.yestechie.com/terms.html'

PC_BASE_URL="${PC_BASE_URL:-https://labs.yestechie.com/install}"; PC_BASE_URL="${PC_BASE_URL%/}"
PC_PORT="${PC_PORT:-8080}"
PC_HOME="${PC_HOME:-$HOME/Library/Application Support/PatrolCraft}"
PC_LABEL="${PC_LABEL:-com.yestechie.patrolcraft}"
PC_RECONFIGURE="${PC_RECONFIGURE:-0}"
PC_NO_SERVICE="${PC_NO_SERVICE:-0}"
PC_FORCE="${PC_FORCE:-0}"
PC_YES="${PC_YES:-0}"

APP_DIR="$PC_HOME/app"
VENV_DIR="$PC_HOME/venv"
LOG_DIR="$PC_HOME/logs"
PLIST="$HOME/Library/LaunchAgents/$PC_LABEL.plist"
MARKER=".patrolcraft-installer"
# Files the app writes at runtime; carried over on upgrade.
KEEP=(.env config.local.yaml data/patrol-points data/preset-thumbs)

MODE=install
VERSION=""
TMP_DIR=""
SHOW_PASS=""

if [ -t 1 ]; then B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; O=$'\033[0m'; else B=""; R=""; G=""; Y=""; O=""; fi
step() { printf '\n%s==>%s %s\n' "$B" "$O" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%sWARN%s %s\n' "$Y" "$O" "$*" >&2; }
die()  { printf '\n%sERROR%s %s\n' "$R" "$O" "$*" >&2; exit 1; }
cleanup() { [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"; return 0; }
trap cleanup EXIT

is_utf8() {  # effective charset: locale charmap, else LC_ALL / LC_CTYPE / LANG
    local cm=""
    cm="$(locale charmap 2>/dev/null)" || cm=""
    if [ -z "$cm" ]; then cm="${LC_ALL:-${LC_CTYPE:-${LANG:-}}}"; fi
    case "$cm" in *UTF-8*|*utf-8*|*UTF8*|*utf8*) return 0 ;; esac
    return 1
}


# Installer banner (Vera, 2026-11-02). Braille mark when the session is UTF-8 and TERM is not
# "linux"; otherwise the console-safe banner. {VERSION} is the version being installed.
IFS= read -r -d '' BANNER_BRAILLE <<'BANNER' || true
┏━━

                        ⠿⠿⠿⣿⣿⠿⠿⠿
                       ⣀⣀⣀⣀⣿⣿⣀⣀⣀⣀
                      ⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇
                      ⠘⠿⠿⠿⠿⠿⠿⠿⠿⠿⠿⠃
               ⢀⣦⣄    ⢸⣿⣿⡿ ⣤⣤ ⢻⣿⣿⡇    ⣠⣴⡀
               ⢸⡿⢿⡁    ⢻⣿⣇⠈⠿⠿⠁⣸⣿⡟    ⢈⡿⢿⡇
                  ⠻⣄    ⠙⠻⠷⣦⣴⠾⠟⠋    ⣠⠟⠁
                   ⠈⠛⢦⣄⡀        ⢀⣠⡴⠟⠁
                      ⠈⠙⠓⠶⠶⠶⠶⠶⠶⠚⠋⠁

      ░█▀█░█▀█░▀█▀░█▀▄░█▀█░█░░░█▀▀░█▀▄░█▀█░█▀▀░▀█▀
      ░█▀▀░█▀█░░█░░█▀▄░█░█░█░░░█░░░█▀▄░█▀█░█▀▀░░█░
      ░▀░░░▀░▀░░▀░░▀░▀░▀▀▀░▀▀▀░▀▀▀░▀░▀░▀░▀░▀░░░░▀░

                     // YESTECHIE LABS
            PTZ Patrol Editor for UniFi Protect

        VERSION  {VERSION}    SOURCE  labs.yestechie.com

                                                         ━━┛
BANNER
IFS= read -r -d '' BANNER_CONSOLE <<'BANNER' || true
┌──

                       ██████████
                           ██
                     ██████████████
                     ██████████████

                     ████  ██  ████
            ███       ███  ██  ███       ███
            ████        ████████        ████
               ███                    ███
                  ████            ████
                      ████████████

      ░███░███░███░██ ░███░█  ░███░██ ░███░███░███
      ░█ █░█ █░ █ ░█ █░█ █░█  ░█  ░█ █░█ █░█  ░ █ 
      ░███░███░░█░░██ ░█░█░█░░░█░░░██ ░███░██░░░█░
      ░█  ░█ █░░█░░█ █░█░█░█  ░█  ░█ █░█ █░█  ░░█░
      ░█░░░█░█░░█░░█░█░███░███░███░█░█░█░█░█░░░░█░

                     // YESTECHIE LABS
            PTZ Patrol Editor for UniFi Protect

        VERSION  {VERSION}    SOURCE  labs.yestechie.com

                                                         ──┘
BANNER

banner() {
    local ver="${1:--}" art line pre rest sp1 val sp2 sp3
    local cb="" cy="" cl="" cm="" cg="" co=""
    # Labs tokens: blue #6388b0, yellow #c69c5e, muted #8c949e, green #7a9f82. Name and URL use bold
    # default text color so they read on light and dark terminals.
    # Off when stdout is not a terminal or NO_COLOR is set (no-color.org). 24-bit when the
    # terminal says so (COLORTERM), else the nearest 256-color index.
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        case "${COLORTERM:-}" in
            truecolor|24bit)
                cb=$'\033[38;2;99;136;176m'; cy=$'\033[38;2;198;156;94m'; cl=$'\033[1m'
                cm=$'\033[38;2;140;148;158m'; cg=$'\033[38;2;122;159;130m' ;;
            *)
                cb=$'\033[38;5;67m'; cy=$'\033[38;5;179m'; cl=$'\033[1m'
                cm=$'\033[38;5;246m'; cg=$'\033[38;5;108m' ;;
        esac
        co=$'\033[0m'
    fi
    if is_utf8 && [ "${TERM:-}" != linux ]; then art="$BANNER_BRAILLE"; else art="$BANNER_CONSOLE"; fi
    art="${art%$'\n'}"
    while IFS= read -r line; do
        if [ -z "$line" ]; then
            printf '\n'
        elif [[ "$line" == *"{VERSION}"* ]]; then
            line="${line//\{VERSION\}/$ver}"
            pre="${line%%VERSION*}"; rest="${line#*VERSION}"
            sp1="${rest%%[! ]*}"; rest="${rest#"$sp1"}"
            val="${rest%% *}"; rest="${rest#"$val"}"
            sp2="${rest%%[! ]*}"; rest="${rest#"$sp2"}"; rest="${rest#SOURCE}"
            sp3="${rest%%[! ]*}"; rest="${rest#"$sp3"}"
            printf '%s%sVERSION%s%s%s%s%s%s%sSOURCE%s%s%s%s%s\n' "$pre" "$cm" "$co" "$sp1" "$cg" "$val" "$co" \
                "$sp2" "$cm" "$co" "$sp3" "$cl" "$rest" "$co"
        elif [[ "$line" == *"// "* ]]; then
            pre="${line%%//*}"; rest="${line#*//}"
            printf '%s%s//%s%s%s%s\n' "$pre" "$cy" "$co" "$cl" "$rest" "$co"
        elif [[ "$line" == *"PTZ Patrol Editor"* ]]; then
            printf '%s%s%s\n' "$cm" "$line" "$co"
        else
            printf '%s%s%s\n' "$cb" "$line" "$co"
        fi
    done <<< "$art"
    printf '\n'
}

ask() {  # ask "prompt" -> reply on stdout; reads the terminal even when piped
    local reply=""
    if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then
        printf '%s' "$1" >/dev/tty; IFS= read -r reply </dev/tty || reply=""
    fi
    printf '%s' "$reply"
}

usage() {
    cat <<EOF
PatrolCraft installer

  Install or upgrade:  curl -fsSL $PC_BASE_URL/patrolcraft.sh | bash
  Uninstall:           curl -fsSL $PC_BASE_URL/patrolcraft.sh | bash -s -- --uninstall
EOF
}

for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE=uninstall ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

# ---------------------------------------------------------------- checks
check_system() {
    if [ "$(uname -s)" != "Darwin" ] && [ "$PC_FORCE" != "1" ]; then
        die "This installer is for macOS."
    fi
    [ "$(id -u)" != "0" ] || die "Run this as your normal user, not with sudo."
    [[ "$PC_PORT" =~ ^[0-9]+$ ]] && [ "$PC_PORT" -ge 1024 ] && [ "$PC_PORT" -le 65535 ] \
        || die "PC_PORT must be a number from 1024 to 65535."
    [[ "$PC_LABEL" =~ ^[A-Za-z0-9.-]+$ ]] || die "PC_LABEL may only use letters, digits, dots and dashes."
    command -v curl >/dev/null || die "curl is missing."
    command -v ssh >/dev/null || die "ssh is missing. PatrolCraft uses it to send per-preset settings to the camera."
    if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
        die "Python 3 needs Apple's Command Line Tools. Run: xcode-select --install   then run this installer again."
    fi
    command -v python3 >/dev/null || die "python3 is missing."
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
        || die "PatrolCraft needs Python 3.9 or newer (found $(python3 -V 2>&1))."
}

check_existing_service() {
    # Never take over a PatrolCraft that this installer didn't set up.
    if [ -f "$PLIST" ] && ! grep -q "$MARKER" "$PLIST" 2>/dev/null; then
        die "A LaunchAgent named $PC_LABEL already exists and was not made by this installer.
       Pick another name and folder, for example:
       curl -fsSL $PC_BASE_URL/patrolcraft.sh | PC_LABEL=com.yestechie.patrolcraft2 PC_HOME=\"\$HOME/PatrolCraft2\" PC_PORT=8090 bash"
    fi
    if [ -d "$APP_DIR" ] && [ ! -f "$PC_HOME/$MARKER" ]; then
        die "$APP_DIR exists but was not made by this installer. Set PC_HOME to another folder."
    fi
}

port_busy() {
    if command -v lsof >/dev/null; then
        lsof -nP -iTCP:"$PC_PORT" -sTCP:LISTEN >/dev/null 2>&1
    else
        (exec 3<>"/dev/tcp/127.0.0.1/$PC_PORT") 2>/dev/null
    fi
}

# ---------------------------------------------------------------- download
resolve_version() {
    if [ -n "${PC_LOCAL_TARBALL:-}" ]; then
        local member
        member="$(tar -tzf "$PC_LOCAL_TARBALL" 2>/dev/null | awk -F/ 'NF==2 && $2=="VERSION"{print; exit}')" || member=""
        VERSION=""
        if [ -n "$member" ]; then VERSION="$(tar -xzOf "$PC_LOCAL_TARBALL" "$member" | tr -d '[:space:]')"; fi
        return
    fi
    if [ -n "${PC_VERSION:-}" ]; then
        VERSION="$PC_VERSION"
    else
        VERSION="$(curl -fsSL "$PC_BASE_URL/patrolcraft-latest" | tr -d '[:space:]')" \
            || die "Could not reach $PC_BASE_URL/patrolcraft-latest. Check the internet connection."
    fi
    [[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]] || die "The release server returned an invalid version ('$VERSION')."
}

sha256_of() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | awk '{print $1}'; else sha256sum "$1" | awk '{print $1}'; fi; }

download_release() {
    TMP_DIR="$(mktemp -d)"
    local archive
    if [ -n "${PC_LOCAL_TARBALL:-}" ]; then
        archive="$PC_LOCAL_TARBALL"
        [ -f "$archive" ] || die "No archive at $archive"
        step "Using local archive $(basename "$archive")"
    else
        local name="patrolcraft-$VERSION.tar.gz"
        archive="$TMP_DIR/$name"
        step "Downloading PatrolCraft $VERSION"
        curl -fsSL -o "$archive" "$PC_BASE_URL/$name" || die "Download failed: $PC_BASE_URL/$name"
        curl -fsSL -o "$archive.sha256" "$PC_BASE_URL/$name.sha256" || die "Download failed: $PC_BASE_URL/$name.sha256"
        local want got
        want="$(awk 'NR==1{print $1}' "$archive.sha256")"
        got="$(sha256_of "$archive")"
        [[ "$want" =~ ^[0-9a-f]{64}$ ]] || die "The checksum file is malformed."
        [ "$want" = "$got" ] || die "Checksum mismatch for $name. Nothing was installed."
        info "Checksum verified."
    fi
    mkdir -p "$TMP_DIR/src"
    tar -xzf "$archive" -C "$TMP_DIR/src" --strip-components=1
    [ -f "$TMP_DIR/src/app/main.py" ] && [ -f "$TMP_DIR/src/requirements.txt" ] || die "The release archive is missing files."
    local inner; inner="$(tr -d '[:space:]' <"$TMP_DIR/src/VERSION")"
    if [ -n "$VERSION" ] && [ "$inner" != "$VERSION" ]; then die "Archive says version '$inner' but '$VERSION' was requested."; fi
    VERSION="$inner"
}

# ---------------------------------------------------------------- install
install_files() {
    step "Installing PatrolCraft $VERSION into $PC_HOME"
    mkdir -p "$PC_HOME" "$LOG_DIR"
    chmod 700 "$PC_HOME"
    touch "$PC_HOME/$MARKER"
    local staged="$PC_HOME/app.new"
    rm -rf "$staged"; cp -R "$TMP_DIR/src" "$staged"
    if [ -d "$APP_DIR" ]; then
        local f
        for f in "${KEEP[@]}"; do
            if [ -e "$APP_DIR/$f" ]; then mkdir -p "$(dirname "$staged/$f")"; cp -Rp "$APP_DIR/$f" "$staged/$f"; fi
        done
        rm -rf "$PC_HOME/app.prev"; mv "$APP_DIR" "$PC_HOME/app.prev"
    fi
    mv "$staged" "$APP_DIR"
    find "$APP_DIR" -name '._*' -delete 2>/dev/null || true
}

install_python() {
    step "Setting up Python packages"
    if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -c 'import sys' 2>/dev/null; then
        rm -rf "$VENV_DIR"; python3 -m venv "$VENV_DIR" || die "Could not create a Python environment."
    fi
    "$VENV_DIR/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
    "$VENV_DIR/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt" \
        || die "Installing Python packages failed. Check the internet connection and run the installer again."
    info "Done."
}

env_get() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -n 1 || true; }

env_set() {  # env_set file NAME value  (value has no newlines)
    local file="$1" name="$2" value="$3" tmp
    tmp="$(mktemp "$file.XXXX")"
    grep -v "^$name=" "$file" >"$tmp" 2>/dev/null || true
    printf '%s=%s\n' "$name" "$value" >>"$tmp"
    mv "$tmp" "$file"; chmod 600 "$file"
}

random_token() { python3 -c "import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))" "$1"; }

write_env() {
    local env="$APP_DIR/.env"
    if [ ! -f "$env" ]; then
        cp "$APP_DIR/.env.example" "$env"; chmod 600 "$env"
    fi
    chmod 600 "$env"
    env_set "$env" APP_HOST 127.0.0.1
    env_set "$env" APP_PORT "$PC_PORT"
    if [ -z "$(env_get "$env" APP_SECRET)" ]; then env_set "$env" APP_SECRET "$(random_token 48)"; fi
    if [ -z "$(env_get "$env" APP_USER)" ]; then env_set "$env" APP_USER admin; fi
    if [ -z "$(env_get "$env" APP_PASS)" ] || [ "$(env_get "$env" APP_PASS)" = "admin" ] || [ "$PC_RECONFIGURE" = "1" ]; then
        SHOW_PASS="$(random_token 12)"
        env_set "$env" APP_PASS "$SHOW_PASS"
    fi
    [ -f "$APP_DIR/config.local.yaml" ] && chmod 600 "$APP_DIR/config.local.yaml"
    return 0
}

xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

write_plist() {
    mkdir -p "$(dirname "$PLIST")"
    local py app logs
    py="$(xml_escape "$VENV_DIR/bin/python")"; app="$(xml_escape "$APP_DIR")"; logs="$(xml_escape "$LOG_DIR")"
    cat >"$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- $MARKER -->
<plist version="1.0">
<dict>
  <key>Label</key><string>$PC_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$py</string><string>-m</string><string>uvicorn</string><string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>$PC_PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$app</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$logs/patrolcraft.log</string>
  <key>StandardErrorPath</key><string>$logs/patrolcraft.log</string>
</dict>
</plist>
PL
    chmod 644 "$PLIST"
}

stop_service() {
    [ "$(uname -s)" = "Darwin" ] || return 0
    launchctl bootout "gui/$(id -u)/$PC_LABEL" >/dev/null 2>&1 || true
}

start_service() {
    step "Starting PatrolCraft"
    if [ "$PC_NO_SERVICE" = "1" ]; then info "Skipped (PC_NO_SERVICE=1)."; return; fi
    stop_service
    sleep 1
    if port_busy; then die "Port $PC_PORT is already in use. Run again with another port, e.g.  curl ... | PC_PORT=8090 bash"; fi
    write_plist
    launchctl bootstrap "gui/$(id -u)" "$PLIST" || die "launchctl could not load $PLIST"

    for _ in $(seq 1 30); do
        if curl -fs -o /dev/null --max-time 2 "http://127.0.0.1:$PC_PORT/api/version"; then info "Running."; return; fi
        sleep 1
    done
    die "PatrolCraft did not start. See $LOG_DIR/patrolcraft.log"
}

finish() {
    local url="http://127.0.0.1:$PC_PORT/"
    printf '\n%sPatrolCraft %s is installed.%s\n\n' "$G" "$VERSION" "$O"
    info "Open:      $url"
    info "Username:  $(env_get "$APP_DIR/.env" APP_USER)"
    if [ -n "$SHOW_PASS" ]; then
        info "Password:  $SHOW_PASS"
        info "Save this password now. It's also in $APP_DIR/.env (only your user can read it)."
    else
        info "Password:  unchanged (new one: curl -fsSL $PC_BASE_URL/patrolcraft.sh | PC_RECONFIGURE=1 bash)"
    fi
    info ""
    info "Then open Config and connect your UniFi console. PatrolCraft runs in demo mode until you do."
    info "Only this Mac can open PatrolCraft. It starts again on its own after a restart."
    if [ "$PC_NO_SERVICE" != "1" ] && [ "$(uname -s)" = "Darwin" ] && [ -z "${SSH_CONNECTION:-}" ]; then open "$url" >/dev/null 2>&1 || true; fi
}

do_install() {
    check_system
    check_existing_service
    resolve_version
    banner "${VERSION:--}"
    if [ ! -d "$APP_DIR" ]; then
        printf '%s\n\n' "$TERMS_NOTICE"
        if [ "$PC_YES" != "1" ]; then
            case "$(ask 'Continue? [y/N] ')" in y|Y|yes|YES) ;; *) die "Cancelled. Nothing was changed." ;; esac
        fi
    fi
    download_release
    if [ -d "$APP_DIR" ]; then stop_service; fi
    install_files
    install_python
    write_env
    start_service
    rm -rf "$PC_HOME/app.prev"
    finish
}

do_uninstall() {
    [ -f "$PC_HOME/$MARKER" ] || [ -f "$PLIST" ] || die "PatrolCraft is not installed at $PC_HOME."
    if [ -f "$PLIST" ] && ! grep -q "$MARKER" "$PLIST"; then die "$PLIST was not made by this installer; leaving it alone."; fi
    if [ "$PC_YES" != "1" ]; then
        case "$(ask "Remove PatrolCraft, its login and saved patrol points from $PC_HOME? [y/N] ")" in y|Y|yes|YES) ;; *) die "Cancelled." ;; esac
    fi
    step "Removing PatrolCraft"
    stop_service
    rm -f "$PLIST"
    [ -f "$PC_HOME/$MARKER" ] && rm -rf "$PC_HOME"
    info "Removed. Your UniFi console and cameras were not changed."
}

if [ "$MODE" = "uninstall" ]; then do_uninstall; else do_install; fi
