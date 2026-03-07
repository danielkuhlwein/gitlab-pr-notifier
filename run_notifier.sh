#!/bin/bash
# ============================================================
# Shell wrapper for the GitlabNotifier applet
# ============================================================
# The osacompile'd applet binary returns exit code 78 (EX_CONFIG)
# even when the AppleScript executes successfully.  This is likely
# because the applet expects to be launched via LaunchServices
# (open -a) rather than invoked directly.
#
# launchd stops scheduling StartInterval jobs after a non-zero
# exit, so we absorb the exit code here and always return 0.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APPLET="${SCRIPT_DIR}/GitlabNotifier.app/Contents/MacOS/applet"

"$APPLET" 2>>"${SCRIPT_DIR}/logs/launchd_stderr.log" || true

exit 0
