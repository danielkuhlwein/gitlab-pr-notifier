#!/bin/bash
# ============================================================
# GitLab PR Notification Manager — Installer
# ============================================================
# This script:
#   1. Checks for Python 3
#   2. Builds the GitlabNotifier.app (osacompile)
#   3. Creates the logs directory
#   4. Verifies the app launches
#   5. Optionally reloads the LaunchAgent (if plist already exists)
#
# NOTE: The LaunchAgent plist (~/Library/LaunchAgents/com.daniel.gitlab-notifier.plist)
# must be created MANUALLY from a clean Terminal session.  Files created by
# sandboxed apps (Claude, Cowork, etc.) inherit com.apple.provenance, which
# causes launchd to block execution.  See README.md for the manual plist step.
#
# IMPORTANT: This project folder must be on a local drive (not iCloud).
# LaunchAgents cannot access iCloud Drive paths due to macOS sandboxing.
#
# Usage:
#   chmod +x install.sh && ./install.sh
#
# To uninstall:
#   ./install.sh --uninstall
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.daniel.gitlab-notifier"
PLIST_DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
NOTIFIER_SCRIPT="${SCRIPT_DIR}/gitlab_notifier.py"
LOG_DIR="${SCRIPT_DIR}/logs"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }

# ------------------------------------------------------------------
# Guard: block installation from iCloud Drive
# ------------------------------------------------------------------
if [[ "$SCRIPT_DIR" == *"Library/Mobile Documents"* ]] || [[ "$SCRIPT_DIR" == *"com~apple~CloudDocs"* ]]; then
    error "This folder is inside iCloud Drive."
    echo "    LaunchAgents cannot access iCloud paths due to macOS sandboxing."
    echo "    Move this folder to a local path first, e.g.:"
    echo "      mv \"$SCRIPT_DIR\" ~/Projects/"
    echo "    Then re-run install.sh from the new location."
    exit 1
fi

# ------------------------------------------------------------------
# Helper: determine current user UID for launchctl bootstrap/bootout
# ------------------------------------------------------------------
CURRENT_UID=$(id -u)
DOMAIN_TARGET="gui/${CURRENT_UID}"

_unload_agent() {
    echo "  Unloading existing LaunchAgent..."
    launchctl bootout "${DOMAIN_TARGET}/${PLIST_NAME}" 2>/dev/null || true
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    launchctl remove "${PLIST_NAME}" 2>/dev/null || true
    sleep 2
}

_load_agent() {
    if launchctl bootstrap "${DOMAIN_TARGET}" "$PLIST_DEST" 2>/dev/null; then
        return 0
    elif launchctl load "$PLIST_DEST" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# ------------------------------------------------------------------
# Uninstall
# ------------------------------------------------------------------
if [[ "${1:-}" == "--uninstall" ]]; then
    echo "Uninstalling GitLab PR Notification Manager..."
    _unload_agent && info "LaunchAgent unloaded" || warn "LaunchAgent was not loaded"
    rm -f "$PLIST_DEST" && info "Plist removed from LaunchAgents"
    echo "Done. The script files in ${SCRIPT_DIR} were left in place."
    echo "Remove them manually if you no longer need them."
    exit 0
fi

echo "=============================================="
echo "  GitLab PR Notification Manager — Installer"
echo "=============================================="
echo

# ------------------------------------------------------------------
# 1. Find the best Python 3 — prefer Homebrew over Xcode CLT
# ------------------------------------------------------------------
PY3_PATH=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$candidate" ]]; then
        PY3_PATH="$candidate"
        break
    fi
done
if [[ -z "$PY3_PATH" ]] && command -v python3 &>/dev/null; then
    PY3_PATH="$(command -v python3)"
fi

if [[ -n "$PY3_PATH" ]]; then
    PY_VERSION=$("$PY3_PATH" --version 2>&1)
    info "Python 3 found: ${PY_VERSION} (${PY3_PATH})"
else
    error "Python 3 not found. Please install Python 3 first."
    exit 1
fi

# ------------------------------------------------------------------
# 2. Create logs directory & clear stale logs from previous installs
# ------------------------------------------------------------------
mkdir -p "$LOG_DIR"
for f in "$LOG_DIR"/launchd_stdout.log "$LOG_DIR"/launchd_stderr.log "$LOG_DIR"/wrapper.log; do
    [[ -f "$f" ]] && : > "$f"
done
STATE_FILE="${SCRIPT_DIR}/.notifier_state.json"
if [[ -f "$STATE_FILE" ]]; then
    : > "$STATE_FILE"
    info "State file cleared (emails will be re-processed for testing)"
fi
info "Logs directory ready: ${LOG_DIR}"

# ------------------------------------------------------------------
# 3. Make scripts executable
# ------------------------------------------------------------------
chmod +x "$NOTIFIER_SCRIPT"
chmod +x "${SCRIPT_DIR}/run_notifier.sh"
info "Made scripts executable"

# ------------------------------------------------------------------
# 4. Build an AppleScript .app wrapper (via osacompile)
# ------------------------------------------------------------------
APP_NAME="GitlabNotifier"
APP_DIR="${SCRIPT_DIR}/${APP_NAME}.app"
WRAPPER_SRC="${SCRIPT_DIR}/wrapper.applescript"
WRAPPER_COMPILED="${SCRIPT_DIR}/.wrapper_compiled.applescript"

rm -rf "$APP_DIR"

sed \
    -e "s|__PYTHON3_PATH__|${PY3_PATH}|g" \
    -e "s|__NOTIFIER_SCRIPT__|${NOTIFIER_SCRIPT}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    "$WRAPPER_SRC" > "$WRAPPER_COMPILED"

osacompile -o "$APP_DIR" "$WRAPPER_COMPILED"

if [[ $? -ne 0 ]]; then
    error "Failed to build ${APP_NAME}.app with osacompile"
    rm -f "$WRAPPER_COMPILED"
    exit 1
fi
rm -f "$WRAPPER_COMPILED"

# Customise the generated Info.plist
PLIST_BUDDY=/usr/libexec/PlistBuddy
APP_INFO_PLIST="${APP_DIR}/Contents/Info.plist"
$PLIST_BUDDY -c "Set :CFBundleIdentifier com.daniel.gitlab-notifier.app" "$APP_INFO_PLIST" 2>/dev/null \
    || $PLIST_BUDDY -c "Add :CFBundleIdentifier string com.daniel.gitlab-notifier.app" "$APP_INFO_PLIST"
$PLIST_BUDDY -c "Add :LSBackgroundOnly bool true" "$APP_INFO_PLIST" 2>/dev/null || true
$PLIST_BUDDY -c "Add :NSAppleEventsUsageDescription string 'GitLab Notifier needs to read Mail.app for new GitLab emails.'" "$APP_INFO_PLIST" 2>/dev/null || true

codesign --sign - --force "$APP_DIR" 2>/dev/null || true

info "Created ${APP_NAME}.app (osacompile + signed)"

# Verify the compiled .app actually launches
APPLET_BIN="${APP_DIR}/Contents/MacOS/applet"
echo "  Verifying compiled app launches correctly..."
rm -f /tmp/gitlab_notifier_wrapper_error.log
VERIFY_EXIT=0
"$APPLET_BIN" 2>/tmp/gitlab_notifier_applet_err.log || VERIFY_EXIT=$?
if [[ $VERIFY_EXIT -ne 0 ]]; then
    warn "Applet exited with code ${VERIFY_EXIT} on verification run (non-zero is normal for applets)"
else
    info "App verification passed (exit 0)"
fi
rm -f /tmp/gitlab_notifier_applet_err.log

# ------------------------------------------------------------------
# 5. Build the Swift notification helper (.app bundle)
# ------------------------------------------------------------------
# GitlabNotifyHelper.app uses UNUserNotificationCenter which supports
# threadIdentifier — this gives proper per-PR grouping in Notification
# Center (the old terminal-notifier approach only supported replacement, not grouping).
HELPER_APP_NAME="GitlabNotifyHelper"
HELPER_APP_DIR="${SCRIPT_DIR}/${HELPER_APP_NAME}.app"
HELPER_SWIFT="${SCRIPT_DIR}/notify_helper.swift"
HELPER_BINARY="${HELPER_APP_DIR}/Contents/MacOS/${HELPER_APP_NAME}"
HELPER_INFO_PLIST="${HELPER_APP_DIR}/Contents/Info.plist"

if [[ -f "$HELPER_SWIFT" ]] && command -v swiftc &>/dev/null; then
    echo "  Building ${HELPER_APP_NAME}.app (Swift notification helper)..."
    rm -rf "$HELPER_APP_DIR"
    mkdir -p "${HELPER_APP_DIR}/Contents/MacOS"

    # Compile
    if swiftc -O -o "$HELPER_BINARY" "$HELPER_SWIFT" \
        -framework Cocoa -framework UserNotifications 2>/tmp/swiftc_err.log; then

        # Create Info.plist (required for UNUserNotificationCenter)
        cat > "$HELPER_INFO_PLIST" <<'INFOPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.daniel.gitlab-notify-helper</string>
    <key>CFBundleName</key>
    <string>GitLab Notifier</string>
    <key>CFBundleDisplayName</key>
    <string>GitLab Notifier</string>
    <key>CFBundleExecutable</key>
    <string>GitlabNotifyHelper</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSUserNotificationAlertStyle</key>
    <string>banner</string>
</dict>
</plist>
INFOPLIST

        # Convert app_icon.png → AppIcon.icns and embed in Resources
        APP_ICON_SRC="${SCRIPT_DIR}/icons/app_ico.png"
        HELPER_RESOURCES="${HELPER_APP_DIR}/Contents/Resources"
        if [[ -f "$APP_ICON_SRC" ]]; then
            echo "  Converting app icon to .icns..."
            ICONSET_DIR=$(mktemp -d)/AppIcon.iconset
            mkdir -p "$ICONSET_DIR"
            for size in 16 32 128 256 512; do
                sips -z $size $size "$APP_ICON_SRC" --out "${ICONSET_DIR}/icon_${size}x${size}.png" &>/dev/null
                double=$((size * 2))
                sips -z $double $double "$APP_ICON_SRC" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" &>/dev/null
            done
            mkdir -p "$HELPER_RESOURCES"
            if iconutil -c icns "$ICONSET_DIR" -o "${HELPER_RESOURCES}/AppIcon.icns" 2>/dev/null; then
                info "App icon embedded in ${HELPER_APP_NAME}.app"
            else
                warn "iconutil failed — app will use default icon"
            fi
            rm -rf "$(dirname "$ICONSET_DIR")"
        else
            warn "icons/app_ico.png not found — skipping app icon"
        fi

        codesign --sign - --force "$HELPER_APP_DIR" 2>/dev/null || true
        info "Built ${HELPER_APP_NAME}.app (Swift, with per-PR notification grouping)"
    else
        warn "Failed to compile ${HELPER_APP_NAME}.app — will fall back to osascript"
        cat /tmp/swiftc_err.log 2>/dev/null
    fi
    rm -f /tmp/swiftc_err.log
else
    if [[ ! -f "$HELPER_SWIFT" ]]; then
        warn "notify_helper.swift not found — skipping Swift helper build"
    elif ! command -v swiftc &>/dev/null; then
        warn "swiftc not found — install Xcode CLT to build the notification helper"
        echo "    Without it, notifications will fall back to osascript (no click-through or grouping)"
    fi
fi

# ------------------------------------------------------------------
# 6. Kill any hung processes from previous installs / verification
# ------------------------------------------------------------------
sleep 1
KILLED_PROCS=0
for proc in applet GitlabNotifyHelper; do
    while read -r pid; do
        kill "$pid" 2>/dev/null && ((KILLED_PROCS++)) || true
    done < <(pgrep -f "$proc" 2>/dev/null || true)
done
if [[ $KILLED_PROCS -gt 0 ]]; then
    info "Killed $KILLED_PROCS hung process(es) from previous install"
fi

# ------------------------------------------------------------------
# 7. Reload the LaunchAgent (if plist already exists)
# ------------------------------------------------------------------
# The plist must be created MANUALLY to avoid com.apple.provenance.
# See README.md for instructions.
if [[ -f "$PLIST_DEST" ]]; then
    # Check for provenance
    if xattr -l "$PLIST_DEST" 2>/dev/null | grep -q "provenance"; then
        warn "com.apple.provenance detected on plist — launchd will block execution!"
        warn "Delete the plist and recreate it manually from a clean Terminal."
        warn "See README.md for instructions."
    else
        _unload_agent
        if _load_agent; then
            info "LaunchAgent reloaded — polling every 30 seconds"
        else
            error "Failed to load LaunchAgent. Try manually:"
            echo "    launchctl bootstrap gui/$(id -u) ${PLIST_DEST}"
        fi

        sleep 2
        if launchctl list "${PLIST_NAME}" &>/dev/null; then
            info "Verified: LaunchAgent is active"
        fi

        echo
        echo "  If macOS asks whether \"${APP_NAME}\" can control Mail.app → click Allow."
        echo
        echo "  Waiting for first run to complete..."
        for i in $(seq 1 15); do
            if [[ -f "$LOG_DIR/wrapper.log" ]] && grep -q "Wrapper AppleScript finished" "$LOG_DIR/wrapper.log" 2>/dev/null; then
                info "First run completed successfully!"
                EMAILS_FOUND=$(grep -o "Found [0-9]* recent" "$LOG_DIR/wrapper.log" 2>/dev/null | tail -1 || echo "unknown")
                echo "    ${EMAILS_FOUND}"
                break
            fi
            sleep 2
        done
    fi
else
    echo
    warn "LaunchAgent plist not found at: ${PLIST_DEST}"
    echo
    echo "  The plist must be created manually to avoid com.apple.provenance."
    echo "  Open Terminal.app from Spotlight (Cmd+Space → Terminal) and paste:"
    echo
    echo "  ────────────────────────────────────────────────────────────"
    cat <<HELPEOF
  cat > ~/Library/LaunchAgents/${PLIST_NAME}.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_DIR}/run_notifier.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>StartInterval</key>
    <integer>30</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/launchd_stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF
HELPEOF
    echo "  ────────────────────────────────────────────────────────────"
    echo
    echo "  Then load it:"
    echo "    launchctl bootstrap gui/$(id -u) ${PLIST_DEST}"
    echo
    echo "  After that, re-run ./install.sh to verify everything works."
    echo
fi

# ------------------------------------------------------------------
# 8. Done
# ------------------------------------------------------------------
echo
info "Installation complete!"
echo
echo "  How it works:"
echo "    • The LaunchAgent runs run_notifier.sh every 30 seconds"
echo "    • run_notifier.sh invokes GitlabNotifier.app (compiled AppleScript)"
echo "    • A Mail.app rule auto-marks GitLab emails as read + moves to 'Gitlab' mailbox"
echo "    • The app reads new emails from the Gitlab mailbox"
echo "    • Emails are classified by gitlab_notifier.py (via --process mode)"
echo "    • Old emails in the Gitlab mailbox are auto-archived after 24 hours"
echo "    • New PR activity triggers a macOS notification"
echo "    • Click the notification to open the PR in your browser"
echo
echo "  Logs (debug):"
echo "    wrapper:   ${LOG_DIR}/wrapper.log      (AppleScript steps — main debug log)"
echo "    python:    ${LOG_DIR}/notifier.log     (classification + notifications)"
echo "    launchd:   ${LOG_DIR}/launchd_stderr.log"
echo
echo "  Quick debug:  tail -f ${LOG_DIR}/wrapper.log ${LOG_DIR}/notifier.log"
echo
echo "  State file: ${SCRIPT_DIR}/.notifier_state.json"
echo
echo "  Commands:"
echo "    Stop:      launchctl bootout gui/$(id -u)/${PLIST_NAME}"
echo "    Start:     launchctl bootstrap gui/$(id -u) ${PLIST_DEST}"
echo "    Uninstall: ./install.sh --uninstall"
echo "    Manual run: ${PY3_PATH} ${NOTIFIER_SCRIPT}"
echo
