# GitLab PR Notification Manager

Polls Apple Mail for GitLab email notifications, classifies them by type, and sends clickable macOS notifications with per-PR grouping.

## How It Works

A macOS **LaunchAgent** runs every 30 seconds and triggers a pipeline:

1. `run_notifier.sh` launches `GitlabNotifier.app` (a compiled AppleScript wrapper)
2. The AppleScript queries Mail.app for unread emails from `gitlab@mg.gitlab.com`
3. Email data is passed to `gitlab_notifier.py`, which parses the raw MIME source and classifies the notification type
4. Notifications are sent via `GitlabNotifyHelper.app` (a Swift helper using `UNUserNotificationCenter`) — this gives proper per-PR grouping in Notification Center and click-to-open-URL support. Falls back to `osascript` if the helper isn't available.
5. Processed emails are marked as read and moved to a local "Gitlab" mailbox in Mail.app
6. Emails older than 24 hours are auto-archived from the Gitlab mailbox to Trash
7. State is persisted in `.notifier_state.json` to avoid duplicate notifications

## Email Classification

The classifier detects notification types from both subject-line suffixes and HTML body parsing:

| Type | Trigger |
|------|---------|
| Review requested | Subject suffix `(Review requested)` or body "requested review" |
| Re-review requested | Subject suffix `(Re-review requested)` |
| Assigned | Subject suffix `(Assigned)` |
| Changes requested | Subject suffix `(Changes requested)` |
| Comment | Subject suffix `(New comment)` or body "commented:" |
| Approved | Subject suffix `(Approved)` or body "Merge request was approved" |
| Merged | Subject suffix `(Merged)` or body "was merged" |
| Closed | Subject suffix `(Closed)` or body "was closed" |
| Mentioned | Subject suffix `(Mentioned)` or body contains username |
| Pipeline failure | Subject contains "Failed pipeline" |
| New commits | Body "pushed new commits to merge request" |
| Draft updated | Body contains "Draft:" |
| Catch-all | Any GitLab email with an MR number not matched above |

## Setup

### Prerequisites

- macOS with Apple Mail configured to receive GitLab email notifications
- Python 3 (Homebrew recommended: `brew install python3`)
- Xcode Command Line Tools (`xcode-select --install`) — needed to compile the Swift notification helper

### 1. Clone and run the installer

```bash
git clone https://github.com/danielkuhlwein/gitlab-pr-notifier.git ~/Projects/gitlab-notifier
cd ~/Projects/gitlab-notifier
chmod +x install.sh && ./install.sh
```

The installer builds `GitlabNotifier.app` (from `wrapper.applescript`) and `GitlabNotifyHelper.app` (from `notify_helper.swift`), checks dependencies, and creates the logs directory. If the LaunchAgent plist already exists and is clean, it reloads it automatically. If not, it prints the exact command you need to run.

> **Important:** This folder must be on a **local drive**, not iCloud Drive. macOS blocks LaunchAgents from accessing iCloud paths. The installer checks for this and warns you.

### 2. Create the LaunchAgent plist (one-time, manual step)

> **Why manual?** macOS applies `com.apple.provenance` to files created by sandboxed apps (Claude, Cowork, IDEs, etc.). `launchd` refuses to execute jobs from provenance-tainted plists. The only reliable workaround is to create the plist from a clean Terminal session.

Open **Terminal.app from Spotlight** (Cmd+Space → "Terminal" → Enter) — not from any IDE. Then paste the following, replacing `<YOUR_PATH>` with the absolute path to this project folder:

```bash
cat > ~/Library/LaunchAgents/com.daniel.gitlab-notifier.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.daniel.gitlab-notifier</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string><YOUR_PATH>/run_notifier.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string><YOUR_PATH></string>
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
    <string><YOUR_PATH>/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string><YOUR_PATH>/logs/launchd_stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF
```

Verify no provenance:

```bash
xattr -l ~/Library/LaunchAgents/com.daniel.gitlab-notifier.plist
# Should show nothing (or just com.apple.macl). NOT com.apple.provenance.
```

Load the agent:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.daniel.gitlab-notifier.plist
```

### 3. Verify

```bash
sleep 35 && launchctl list com.daniel.gitlab-notifier | grep LastExitStatus
```

`LastExitStatus` should be `0`. If macOS asks whether "GitlabNotifier" can control Mail.app, click **Allow**.

## Configuration

Key settings are at the top of `gitlab_notifier.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MY_NAME` | `"Daniel Kuhlwein"` | Used to detect when you're the PR author |
| `MY_GITLAB_USERNAME` | `"daniel-kuhlwein"` | Used to detect @-mentions |
| `GITLAB_EMAIL_SENDER` | `"gitlab@mg.gitlab.com"` | Email sender to filter on |
| `EMAIL_LOOKBACK_HOURS` | `2` | How far back to scan for emails |
| `GITLAB_MAILBOX` | `"Gitlab"` | Local Mail.app mailbox name |
| `GITLAB_FOLDER_TTL_HOURS` | `24` | How long emails stay before auto-archiving |

## Debugging

```bash
# Watch logs in real time
tail -f logs/wrapper.log logs/notifier.log

# Enable verbose DEBUG logging for a manual run
GITLAB_NOTIFIER_DEBUG=1 ./run_notifier.sh

# Check launchd system log for errors
log show --predicate 'sender == "launchd" AND composedMessage CONTAINS "gitlab"' --last 5m --style compact

# Check for posix_spawn / permission errors
log show --predicate 'sender == "launchd" AND messageType == 16' --last 5m --style compact | grep gitlab

# Manual run (bypasses launchd entirely)
./run_notifier.sh
```

To enable debug logging permanently, add this to the plist's `EnvironmentVariables` dict (you'll need to recreate the plist from a clean Terminal):

```xml
<key>GITLAB_NOTIFIER_DEBUG</key>
<string>1</string>
```

## Testing

Run the classifier test suite:

```bash
python3 test_classifier.py
```

This runs 18 test cases covering all observed email types (including MIME-encoded bodies) and URL extraction.

## Uninstall

```bash
./install.sh --uninstall
```

## Project Structure

```
.
├── install.sh                  # Installer (builds apps, checks deps, reloads agent)
├── run_notifier.sh             # LaunchAgent wrapper (absorbs applet exit codes)
├── wrapper.applescript         # AppleScript source — queries Mail.app
├── gitlab_notifier.py          # Python classifier + notification dispatcher
├── notify_helper.swift         # Swift notification sender (UNUserNotificationCenter)
├── com.daniel.gitlab-notifier.plist  # LaunchAgent template (reference only)
├── test_classifier.py          # Test suite for the email classifier
├── icons/                      # Notification icons + app icon
│   └── app_ico.png             # App icon (embedded by install.sh)
├── CLAUDE.md                   # AI assistant instructions
└── .gitignore
```
