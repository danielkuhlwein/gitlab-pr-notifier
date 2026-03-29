# CLAUDE.md — Rules for AI assistants working on this project

## Critical: com.apple.provenance and LaunchAgent plists

**NEVER create, write, modify, or overwrite the LaunchAgent plist file.**

The file `~/Library/LaunchAgents/com.daniel.gitlab-notifier.plist` must be created
manually by the user from a clean Terminal.app session (launched from Spotlight/Finder,
not from any IDE or sandboxed app).

**Why:** macOS applies `com.apple.provenance` to files created by sandboxed applications
(Claude, Cowork, VS Code, etc.). launchd refuses to execute jobs from
provenance-tainted plists with `posix_spawn: Operation not permitted`. This attribute
cannot be removed without disabling SIP. The only workaround is for the user to create
the file themselves from an untainted process.

**What this means in practice:**
- `install.sh` builds the .app, scripts, and logs — but does NOT touch the plist
- If the plist doesn't exist, `install.sh` prints the exact command for the user to paste
- If the plist exists and is clean, `install.sh` reloads it
- If the plist exists but has provenance, `install.sh` warns the user to recreate it
- The template `com.daniel.gitlab-notifier.plist` in the project dir is reference only

## Project structure

- `wrapper.applescript` — AppleScript that talks to Mail.app (compiled into .app by install.sh)
- `gitlab_notifier.py` — Python script for email classification and notifications
- `notify_helper.swift` — Swift notification sender (compiled into .app by install.sh)
- `run_notifier.sh` — Shell wrapper that runs the applet and absorbs its exit code
- `install.sh` — Builds .app bundles, checks deps, reloads agent (does NOT create plist)
- `com.daniel.gitlab-notifier.plist` — Template/reference only (not installed directly)
- `test_classifier.py` — Test suite for the email classifier
- `icons/` — App icon (`app_icon.png`) and notification-type icons
- `GitlabNotifier.app/` — Built artifact (compiled by osacompile, do not edit)
- `GitlabNotifyHelper.app/` — Built artifact (compiled from notify_helper.swift, do not edit)

## Critical: dual code paths in gitlab_notifier.py

`gitlab_notifier.py` has **two separate entry points** that both process emails and send notifications:

1. **`main()`** — used by `./run_notifier.sh` for direct/manual runs
2. **`process_mode(input_path, output_path)`** — used by `wrapper.applescript` via `--process` flag (this is the launchd path)

**When modifying notification logic (classification, title formatting, sender extraction, etc.), you MUST update BOTH code paths.** The `--process` mode is what actually runs in production via launchd. The `main()` path is only used for manual testing.

## Key paths (with placeholders in source files)

- `__PYTHON3_PATH__` → resolved by install.sh (e.g. `/opt/homebrew/bin/python3`)
- `__NOTIFIER_SCRIPT__` → resolved by install.sh (full path to `gitlab_notifier.py`)
- `__LOG_DIR__` → resolved by install.sh (full path to `logs/`)
- `__SCRIPT_DIR__` / `__APP_DIR__` → used in plist template (reference only)

## Testing

- Run classifier tests: `python3 test_classifier.py`
- Manual run: `./run_notifier.sh` (runs the full pipeline without launchd)
- Check logs: `tail -f logs/wrapper.log logs/notifier.log`
- LaunchAgent status: `launchctl list com.daniel.gitlab-notifier`
