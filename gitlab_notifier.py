#!/usr/bin/env python3
"""
GitLab PR Notification Manager
===============================
Polls Apple Mail for unread GitLab emails, classifies them, sends
clickable macOS notifications (via GitlabNotifyHelper), and logs everything.

Architecture
------------
- Triggered every 30s by a LaunchAgent (replaces unreliable Mail rules).
- Reads *unread* emails from Mail.app via AppleScript.
- Tracks already-processed message IDs in a JSON state file so each
  email is only notified once.
- Sends macOS notifications with click-through to the GitLab PR URL
  using GitlabNotifyHelper (falls back to osascript if unavailable).
- Writes structured logs per-run into a logs/ directory alongside
  this script, plus a rolling main log.
"""

import email as email_lib
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MY_NAME = "Daniel Kuhlwein"
MY_GITLAB_USERNAME = "daniel-kuhlwein"
GITLAB_EMAIL_SENDER = "gitlab@mg.gitlab.com"

# Directories (relative to this script's location)
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / ".notifier_state.json"
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "notifier.log"

# How far back to look for emails (hours)
EMAIL_LOOKBACK_HOURS = 2

# Name of the local Mail.app mailbox to file processed emails into
GITLAB_MAILBOX = "Gitlab"

# How long to keep emails in the Gitlab mailbox before archiving (hours)
GITLAB_FOLDER_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """Configure logging to main log file and stdout.

    Set the environment variable GITLAB_NOTIFIER_DEBUG=1 to enable
    DEBUG-level output (useful for troubleshooting).  The default
    level is INFO.
    """
    LOG_DIR.mkdir(exist_ok=True)

    level = logging.DEBUG if os.environ.get("GITLAB_NOTIFIER_DEBUG") == "1" else logging.INFO

    logger = logging.getLogger("gitlab_notifier")
    logger.setLevel(level)

    # Formatter
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Main rolling log (append)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 2. Stdout (captured by launchd when run via the LaunchAgent)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# State management — avoid duplicate notifications
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load the set of already-notified email message IDs."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"notified_ids": [], "last_run": None}
    return {"notified_ids": [], "last_run": None}


def save_state(state: dict):
    """Persist state, keeping only the last 500 message IDs."""
    state["notified_ids"] = state["notified_ids"][-500:]
    state["last_run"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Fetch emails from Apple Mail via AppleScript
# ---------------------------------------------------------------------------

FETCH_APPLESCRIPT = textwrap.dedent("""\
    tell application "Mail"
        set lookbackDate to (current date) - ({hours} * hours)
        -- Query the Gitlab mailbox (populated by Mail.app rule).
        -- We fetch ALL recent messages (not just unread) and rely on
        -- the Python state file to skip already-processed ones.
        set targetBox to mailbox "{mailbox}"
        set gitlabMessages to (messages of targetBox whose ¬
            sender contains "{sender}" and ¬
            date received > lookbackDate)

        set output to ""
        repeat with msg in gitlabMessages
            try
                set msgId to id of msg as string
                set msgSubject to subject of msg
                set msgSender to sender of msg
                set msgDate to date received of msg as string
                set msgSource to source of msg

                set output to output & "<<<MSG_START>>>" & return
                set output to output & "ID: " & msgId & return
                set output to output & "SUBJECT: " & msgSubject & return
                set output to output & "FROM: " & msgSender & return
                set output to output & "DATE: " & msgDate & return
                set output to output & "<<<SOURCE_START>>>" & return
                set output to output & msgSource & return
                set output to output & "<<<SOURCE_END>>>" & return
                set output to output & "<<<MSG_END>>>" & return
            end try
        end repeat

        return output
    end tell
""")


def fetch_gitlab_emails(logger: logging.Logger) -> str:
    """Execute AppleScript to pull unread GitLab emails from Mail.app."""
    script = FETCH_APPLESCRIPT.format(
        hours=EMAIL_LOOKBACK_HOURS,
        sender=GITLAB_EMAIL_SENDER,
        mailbox=GITLAB_MAILBOX,
    )
    logger.debug("Executing AppleScript to fetch emails (lookback=%dh)", EMAIL_LOOKBACK_HOURS)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0:
            logger.warning("AppleScript stderr: %s", result.stderr.strip())
        raw = result.stdout
        logger.debug("AppleScript returned %d bytes", len(raw))
        return raw
    except subprocess.TimeoutExpired:
        logger.error("AppleScript timed out after 45s")
        return ""
    except Exception as e:
        logger.error("Failed to run AppleScript: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Mail.app mailbox management — mark read, move, and TTL cleanup
# ---------------------------------------------------------------------------

def mark_read_and_move(msg_ids: list[str], logger: logging.Logger):
    """
    No-op: Mail.app rule now handles mark-as-read and move to Gitlab mailbox.

    Kept for API compatibility with callers. Only logs the count of
    processed IDs for debugging.
    """
    if not msg_ids:
        return
    logger.info(
        "Mail.app rule handles mark-read/move; %d email(s) processed this run",
        len(msg_ids),
    )


def cleanup_gitlab_folder(logger: logging.Logger):
    """
    Delete (archive) emails in the Gitlab mailbox older than the TTL.

    Mail.app's 'delete' moves messages to Trash, which acts as an archive
    the user can recover from if needed before Trash is emptied.
    """
    script = textwrap.dedent(f"""\
        tell application "Mail"
            try
                set targetBox to mailbox "{GITLAB_MAILBOX}"
                set cutoffDate to (current date) - ({GITLAB_FOLDER_TTL_HOURS} * hours)
                set deletedCount to 0

                -- Collect messages to delete (iterate in reverse to avoid index shift)
                set msgList to messages of targetBox
                repeat with i from (count of msgList) to 1 by -1
                    try
                        set msg to item i of msgList
                        if date received of msg < cutoffDate then
                            delete msg
                            set deletedCount to deletedCount + 1
                        end if
                    end try
                end repeat

                return deletedCount as string
            on error errMsg
                return "error: " & errMsg
            end try
        end tell
    """)

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if output.startswith("error:"):
            logger.warning("Gitlab folder cleanup: %s", output)
        elif output and output != "0":
            logger.info("Archived %s email(s) from '%s' folder (>%dh old)",
                        output, GITLAB_MAILBOX, GITLAB_FOLDER_TTL_HOURS)
        else:
            logger.debug("Gitlab folder cleanup: nothing to archive")
    except Exception as e:
        logger.error("Failed to clean up Gitlab folder: %s", e)


# ---------------------------------------------------------------------------
# Parse raw AppleScript output into structured email dicts
# ---------------------------------------------------------------------------

def parse_emails(raw: str, logger: logging.Logger) -> list[dict]:
    """Split the raw AppleScript dump into a list of email dicts."""
    emails = []
    blocks = raw.split("<<<MSG_START>>>")

    for block in blocks:
        if "<<<MSG_END>>>" not in block:
            continue

        email: dict = {}
        # Extract simple header fields
        for prefix, key in [
            ("ID: ", "id"),
            ("SUBJECT: ", "subject"),
            ("FROM: ", "from"),
            ("DATE: ", "date"),
        ]:
            match = re.search(rf"^{re.escape(prefix)}(.+)$", block, re.MULTILINE)
            if match:
                email[key] = match.group(1).strip()

        # Extract raw email source (MIME/HTML)
        src_match = re.search(
            r"<<<SOURCE_START>>>\s*(.+?)\s*<<<SOURCE_END>>>", block, re.DOTALL
        )
        if src_match:
            email["source"] = src_match.group(1)

        if email.get("id") and email.get("subject"):
            emails.append(email)

    logger.info("Parsed %d unread GitLab email(s)", len(emails))
    return emails


# ---------------------------------------------------------------------------
# MIME decoding — extract readable text from raw email source
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML→text converter: strips tags, keeps text content."""

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []

    def handle_data(self, data):
        self._pieces.append(data)

    def get_text(self) -> str:
        return " ".join(self._pieces)


def _html_to_text(html: str) -> str:
    """Best-effort HTML → plain text (no external deps)."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        # If parsing fails, strip tags with a regex fallback
        return re.sub(r"<[^>]+>", " ", html)


def decode_mime_source(raw_source: str) -> str:
    """
    Decode a raw MIME email source into readable plain text.

    AppleScript's `source of msg` returns the full RFC-2822 message,
    which typically contains base64- or quoted-printable-encoded HTML.
    We parse the MIME structure, decode the body parts, strip HTML tags,
    and return concatenated text suitable for regex classification.

    If the source isn't valid MIME (e.g. already plain text in tests),
    it is returned as-is.
    """
    if not raw_source:
        return ""

    # Quick heuristic: if it doesn't look like MIME, return as-is.
    # Real MIME has headers like "Content-Type:" somewhere in the
    # header block.  Gmail/Google Workspace emails can have very long
    # DKIM/ARC headers that push Content-Type well past 2 KB, so we
    # search a generous prefix (10 KB covers even the heaviest headers).
    if "Content-Type:" not in raw_source[:10000]:
        return raw_source

    try:
        msg = email_lib.message_from_string(raw_source)
    except Exception:
        return raw_source

    text_parts: list[str] = []

    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue

        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue

        if ctype == "text/html":
            decoded = _html_to_text(decoded)

        text_parts.append(decoded)

    if text_parts:
        return "\n".join(text_parts)

    # Fallback: the message_from_string parsed it but found no text parts.
    return raw_source


# ---------------------------------------------------------------------------
# Email classification — what type of GitLab notification is this?
# ---------------------------------------------------------------------------

# Dataclass-like named tuple for classification results
class Classification:
    __slots__ = ("type", "icon", "title", "pr_title", "priority", "project", "mr_number")

    def __init__(self, type_: str, icon: str, title: str, pr_title: str, priority: int = 5,
                 project: str = "", mr_number: str = ""):
        self.type = type_
        self.icon = icon           # emoji for this notification type
        self.title = title         # human-readable action (e.g. "Review Requested")
        self.pr_title = pr_title   # PR description (e.g. "feat: semantic releases")
        self.priority = priority   # lower = more important
        self.project = project     # repo name (e.g. "cav-ts-apps-tools")
        self.mr_number = mr_number # MR number without ! (e.g. "942")

    def __repr__(self):
        return f"Classification({self.type!r}, {self.title!r})"

    @property
    def notify_title(self) -> str:
        """Notification title: emoji + action (e.g. '📋 Review Requested')."""
        return f"{self.icon} {self.title}"

    @property
    def notify_body(self) -> str:
        """Notification body: repo name on first line, PR description on second."""
        desc = self.pr_title
        if len(desc) > 120:
            desc = desc[:117] + "..."
        if self.project:
            return f"{self.project}\n{desc}"
        return desc

    @property
    def group_id(self) -> str:
        """Composite group ID: repo + MR number, so related notifications stack."""
        if self.project and self.mr_number:
            return f"gitlab-{self.project}-!{self.mr_number}"
        elif self.project:
            return f"gitlab-{self.project}"
        return f"gitlab-{self.type}"

    @property
    def notification_id(self) -> str:
        """ID for notification replacement: same type + same PR = replace old.

        Different types for the same PR (e.g. 'Review Requested' and
        'PR Approved') each get their own notification, but a second
        'PR Activity' for the same PR replaces the first.
        """
        return f"{self.type}-{self.group_id}"


def classify_email(email: dict, logger: logging.Logger) -> Classification | None:
    """
    Determine the notification type for a GitLab email.

    Strategy:
    1. Parse project, MR number, and PR title from the subject line.
    2. Check the email source (HTML body) for GitLab action keywords.
    3. Also check subject-line action suffixes where GitLab adds them.

    Returns None for emails that aren't worth notifying about.
    """
    subject = email.get("subject", "")
    source = email.get("source", "")
    logger.debug("Classifying: %s", subject)

    # --- Extract PR metadata from subject ---
    # Patterns observed:
    #   "Re: cav-ts-apps-tools | fix(ioo): live video ... (!891)"
    #   "Re: Deployments | feat: bump apps-tools (!374)"
    #   "Re: Infrastructure | feat: scoping ... (!193)"
    #   "cav-ts-apps-tools | Failed pipeline for main | b475448f"
    #   "Deployments | feat: targeting renamed slackbot token (!415)"

    mr_match = re.search(r"\(!(\d+)\)", subject)
    mr_number = mr_match.group(1) if mr_match else None

    # Project | description (!NNN)
    proj_match = re.search(r"^(?:Re:\s*)?([^|]+)\|\s*(.+?)(?:\s*\(!?\d+\))?$", subject)
    if proj_match:
        project = proj_match.group(1).strip()
        pr_title = proj_match.group(2).strip()
        # Clean trailing action hints like "(Merged)" from pr_title
        pr_title = re.sub(r"\s*\([^)]+\)\s*$", "", pr_title).strip()
    else:
        project = "GitLab"
        pr_title = subject

    # ------------------------------------------------------------------
    # 1. Pipeline failure (special — no MR number)
    # ------------------------------------------------------------------
    if "failed pipeline" in subject.lower():
        pipeline_match = re.search(r"\|\s*Failed pipeline for (\S+)\s*\|\s*(\w+)", subject)
        branch = pipeline_match.group(1) if pipeline_match else "unknown"
        sha = pipeline_match.group(2) if pipeline_match else ""
        return Classification(
            "pipeline_failure", "🔴",
            "Pipeline Failed",
            f"{branch} ({sha[:8]})",
            priority=2,
            project=project, mr_number=mr_number or "",
        )

    # ------------------------------------------------------------------
    # 2. Subject-line action suffixes (when GitLab adds them)
    # ------------------------------------------------------------------
    subject_actions = [
        (r"\(review requested\)",       "review_requested",  "📋", "Review Requested",     1),
        (r"\(re-review requested\)",    "rereview_requested","🔄", "Re-Review Requested",  1),
        (r"\(assigned\)",               "assigned",          "📌", "PR Assigned to You",   1),
        (r"\(reassigned\)",             "reassigned",        "📌", "PR Reassigned",        3),
        (r"\(changes requested\)",      "changes_requested", "🔧", "Changes Requested",    2),
        (r"\(new comment\)",            "comment",           "💬", "New Comment",           3),
        (r"\(comment edited\)",         "comment_edited",    "💬", "Comment Edited",        4),
        (r"\(approved\)",               "approved",          "✅", "PR Approved",           3),
        (r"\(merged\)",                 "merged",            "🔀", "PR Merged",             4),
        (r"\(closed\)",                 "closed",            "🔴", "PR Closed",             4),
        (r"\(mentioned\)",              "mentioned",         "👋", "You Were Mentioned",   2),
    ]

    for pattern, type_, icon, title, prio in subject_actions:
        if re.search(pattern, subject, re.IGNORECASE):
            return Classification(type_, icon, title, pr_title, prio,
                                  project=project, mr_number=mr_number or "")

    # ------------------------------------------------------------------
    # 3. MR lifecycle emails — suppress noise, promote relevant ones
    #    When a new MR is created, GitLab fires 3 emails simultaneously:
    #      a) "X created a merge request" (generic, lists reviewers)
    #      b) "X was added as an assignee"
    #      c) "X, Y, Z were added as reviewers"
    #    We suppress the redundant ones and only notify for actions
    #    directed at you personally.
    # ------------------------------------------------------------------
    # Decode the MIME source so base64/quoted-printable bodies become
    # searchable plain text, then lowercase for easier matching.
    decoded_source = decode_mime_source(source) if source else ""
    src_lower = decoded_source[:8000].lower() if decoded_source else ""

    # "X was added as an assignee" — only notify if YOU were assigned
    if re.search(r"was added as an assignee", src_lower):
        if re.search(
            re.escape(MY_NAME.lower()) + r".*was added as an assignee",
            src_lower,
        ):
            return Classification("assigned", "📌", "PR Assigned to You", pr_title, priority=1,
                                  project=project, mr_number=mr_number or "")
        logger.info("Suppressing assignee email (not you): %s", subject)
        return None

    # "X, Y, and Z were added as reviewers" — notify only if YOUR name
    # appears in the reviewer list
    if re.search(r"(?:was|were) added as (?:a )?reviewers?", src_lower):
        if MY_NAME.lower() in src_lower:
            return Classification("review_requested", "📋", "Review Requested", pr_title, priority=1,
                                  project=project, mr_number=mr_number or "")
        logger.info("Suppressing reviewer email (not you): %s", subject)
        return None

    # "X created a merge request" — suppress entirely.
    # The reviewer-added or assignee-added emails above are more
    # actionable; this one is always redundant when they follow.
    if re.search(r"created a merge request", src_lower):
        logger.info("Suppressing MR-creation email (redundant): %s", subject)
        return None

    # ------------------------------------------------------------------
    # 4. Body-based detection (for emails where subject has no suffix)
    #    We search the raw MIME source for GitLab's templated phrases.
    # ------------------------------------------------------------------
    body_actions = [
        # Approval (rich HTML email with "Merge request was approved")
        (r"merge request was approved",                  "approved",       "✅", "PR Approved",            3),
        # Merged
        (r"merge request .{0,200}was merged",            "merged",         "🔀", "PR Merged",              4),
        # Closed
        (r"merge request .{0,200}was closed",            "closed",         "🔴", "PR Closed",              4),
        # Someone commented
        (r"(?:commented|noted):",                        "comment",        "💬", "New Comment",             3),
        # New commits pushed
        (r"pushed new commits? to merge request",       "new_commits",    "📦", "New Commits Pushed",     5),
        # Review requested (body-based, e.g. "requested review from X")
        (r"requested review",                            "review_requested","📋","Review Requested",       1),
        # Draft / WIP opened
        (r"draft:?\s",                                   "draft",          "📝", "Draft PR Updated",       6),
        # Someone mentioned you specifically
        (re.escape(MY_GITLAB_USERNAME),                  "mentioned",      "👋", "You Were Mentioned",     2),
    ]

    for pattern, type_, icon, title, prio in body_actions:
        if re.search(pattern, src_lower):
            return Classification(type_, icon, title, pr_title, prio,
                                  project=project, mr_number=mr_number or "")

    # ------------------------------------------------------------------
    # 5. Catch-all: if it's a GitLab email with an MR number we haven't
    #    classified, still notify but at low priority.
    # ------------------------------------------------------------------
    if mr_number:
        return Classification(
            "pr_activity", "🔔", "PR Activity", pr_title, priority=7,
            project=project, mr_number=mr_number,
        )

    logger.debug("Unclassified email (no MR number): %s", subject)
    return None


# ---------------------------------------------------------------------------
# Extract the GitLab URL from the email
# ---------------------------------------------------------------------------

def extract_pr_url(email: dict, logger: logging.Logger) -> str | None:
    """
    Pull the GitLab merge_requests URL from the email source.

    Priority:
    1. Explicit merge_requests URL in the HTML source
    2. Explicit pipelines URL for pipeline failures
    3. "view it on GitLab" link
    """
    raw_source = email.get("source", "")
    subject = email.get("subject", "")

    if not raw_source:
        logger.debug("No email source available for URL extraction")
        return None

    # Decode MIME so URLs inside base64/QP bodies are searchable.
    # Also search the raw source as a fallback (URLs may appear in
    # plain-text MIME parts or headers without encoding).
    decoded = decode_mime_source(raw_source)
    source = f"{raw_source}\n{decoded}" if decoded != raw_source else raw_source

    # 1. Merge request URL
    mr_url = re.search(
        r'https://gitlab\.com/[^\s"\'<>]+/merge_requests/\d+', source
    )
    if mr_url:
        url = mr_url.group(0)
        # Strip any trailing HTML artifacts
        url = re.sub(r'["\'>].*', "", url)
        logger.debug("Found MR URL: %s", url)
        return url

    # 2. Pipeline URL
    pipe_url = re.search(
        r'https://gitlab\.com/[^\s"\'<>]+/pipelines/\d+', source
    )
    if pipe_url:
        url = pipe_url.group(0)
        url = re.sub(r'["\'>].*', "", url)
        logger.debug("Found pipeline URL: %s", url)
        return url

    # 3. Generic "view it on GitLab" link
    view_url = re.search(
        r'href="(https://gitlab\.com/[^\s"]+)"[^>]*>\s*view it on GitLab',
        source,
        re.IGNORECASE,
    )
    if view_url:
        logger.debug("Found 'view on GitLab' URL: %s", view_url.group(1))
        return view_url.group(1)

    logger.debug("No URL found in email source")
    return None


# ---------------------------------------------------------------------------
# macOS notifications
# ---------------------------------------------------------------------------

def _find_notify_helper() -> str | None:
    """
    Resolve the path to GitlabNotifyHelper.app's binary.

    This is the preferred notification sender: it uses
    UNUserNotificationCenter with threadIdentifier, giving proper
    per-PR visual grouping in Notification Center.
    """
    helper = SCRIPT_DIR / "GitlabNotifyHelper.app" / "Contents" / "MacOS" / "GitlabNotifyHelper"
    if helper.is_file() and os.access(helper, os.X_OK):
        return str(helper)
    return None


# Cache the resolved path at module level
_NOTIFY_HELPER_PATH: str | None = None


def _get_notify_helper() -> str | None:
    global _NOTIFY_HELPER_PATH
    if _NOTIFY_HELPER_PATH is None:
        _NOTIFY_HELPER_PATH = _find_notify_helper() or ""
    return _NOTIFY_HELPER_PATH or None


def send_notification(
    title: str,
    message: str,
    url: str | None = None,
    group_id: str = "gitlab",
    notification_id: str | None = None,
    subtitle: str = "",
    image: str | None = None,
    logger: logging.Logger | None = None,
):
    """
    Send a macOS notification.

    Uses GitlabNotifyHelper.app (Swift, UNUserNotificationCenter) for
    proper per-PR grouping via threadIdentifier, click → open URL,
    and subtitle support.  Falls back to osascript if the helper is
    not available (no click-through or grouping in that case).
    """
    helper = _get_notify_helper()
    if helper:
        cmd = [
            helper,
            "-title", title,
            "-message", message,
            "-group", group_id,
        ]
        if subtitle:
            cmd.extend(["-subtitle", subtitle])
        if notification_id:
            cmd.extend(["-identifier", notification_id])
        if url:
            cmd.extend(["-open", url])
        if image:
            cmd.extend(["-image", image])

        # Log stderr to a file so we can diagnose permission issues etc.
        helper_log = LOG_DIR / "notify_helper.log"
        if logger:
            logger.info("notify-helper cmd: %s", cmd)
        try:
            stderr_fh = open(helper_log, "a")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh,
            )
            if logger:
                logger.info(
                    "Sent notification (GitlabNotifyHelper, PID %d): %s — %s [thread=%s]",
                    proc.pid, title, message, group_id,
                )
            return
        except FileNotFoundError:
            if logger:
                logger.warning("GitlabNotifyHelper binary missing, falling back to osascript")
        except Exception as e:
            if logger:
                logger.warning("GitlabNotifyHelper failed (%s), falling back to osascript", e)

    # Fallback: osascript (no click-through, no grouping, but always works)
    escaped_body = message.replace('"', '\\"').replace("'", "'")
    escaped_title = title.replace('"', '\\"').replace("'", "'")
    script = f'display notification "{escaped_body}" with title "{escaped_title}" sound name "default"'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, capture_output=True)
        if logger:
            logger.info("Sent notification (osascript): %s — %s", title, message)
            if url:
                logger.info("URL (open manually — osascript can't attach click actions): %s", url)
    except Exception as e:
        if logger:
            logger.error("osascript notification failed: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("GitLab Notifier run started")

    # Load state
    state = load_state()
    notified_ids = set(state.get("notified_ids", []))
    logger.info("State loaded: %d previously notified IDs", len(notified_ids))

    # Check notification sender availability
    helper = _get_notify_helper()
    if helper:
        logger.info("GitlabNotifyHelper found: %s (per-PR grouping enabled)", helper)
    else:
        logger.warning(
            "GitlabNotifyHelper not found — notifications won't be clickable or grouped. "
            "Run install.sh to build it."
        )

    # Fetch emails
    raw = fetch_gitlab_emails(logger)
    if not raw.strip():
        logger.info("No new GitLab emails in '%s' mailbox. Done.", GITLAB_MAILBOX)
        save_state({"notified_ids": list(notified_ids), "last_run": None})
        return

    # Parse
    emails = parse_emails(raw, logger)
    if not emails:
        logger.info("No parseable emails. Done.")
        save_state({"notified_ids": list(notified_ids), "last_run": None})
        return

    # Process each email
    new_notifications = 0
    skipped_already_notified = 0
    skipped_unclassified = 0
    ids_to_move: list[str] = []  # message IDs to mark read + move to Gitlab folder

    for email in emails:
        msg_id = email.get("id", "")

        # Skip already-notified
        if msg_id in notified_ids:
            skipped_already_notified += 1
            logger.debug("Already notified, skipping: %s", email.get("subject", "?"))
            continue

        # Classify
        classification = classify_email(email, logger)
        if classification is None:
            skipped_unclassified += 1
            logger.debug("Unclassified, skipping: %s", email.get("subject", "?"))
            notified_ids.add(msg_id)  # Don't re-process
            ids_to_move.append(msg_id)  # Still move unclassified GitLab emails
            continue

        # Extract URL
        url = extract_pr_url(email, logger)

        # Log the classification
        logger.info(
            "NOTIFY [%s] %s — %s | %s (url=%s)",
            classification.type,
            classification.title,
            classification.project,
            classification.pr_title,
            url or "none",
        )

        # Send notification (with per-type icon if available)
        icon_path = SCRIPT_DIR / "icons" / f"{classification.title}.png"
        send_notification(
            classification.notify_title,
            classification.notify_body,
            url=url,
            group_id=classification.group_id,
            notification_id=classification.notification_id,
            image=str(icon_path) if icon_path.exists() else None,
            logger=logger,
        )

        # Mark as notified and queue for move
        notified_ids.add(msg_id)
        ids_to_move.append(msg_id)
        new_notifications += 1

    # Mark processed emails as read and move to Gitlab folder
    if ids_to_move:
        mark_read_and_move(ids_to_move, logger)

    # Clean up old emails in the Gitlab folder (24h TTL)
    cleanup_gitlab_folder(logger)

    # Summary
    logger.info(
        "Run complete: %d new notification(s), %d already notified, %d suppressed, %d processed",
        new_notifications,
        skipped_already_notified,
        skipped_unclassified,
        len(ids_to_move),
    )

    # Save state
    save_state({"notified_ids": list(notified_ids)})
    logger.info("State saved. Done.")


def process_from_file(input_path: str, output_path: str):
    """
    Process pre-fetched email data from a file (written by the wrapper
    AppleScript).  This mode does NOT call osascript — all Mail.app
    interaction is handled by the AppleScript wrapper, which has proper
    macOS Automation (TCC) permission.

    - Reads raw email dump from *input_path* (same <<<MSG_START>>> format
      that fetch_gitlab_emails / AppleScript produces).
    - Classifies each email and sends macOS notifications.
    - Writes the message IDs that should be marked-as-read / moved to
      *output_path* (one ID per line) so the wrapper can do the Mail
      operations.
    """
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("GitLab Notifier run started (--process mode)")

    # Load state
    state = load_state()
    notified_ids = set(state.get("notified_ids", []))
    logger.info("State loaded: %d previously notified IDs", len(notified_ids))

    # Check notification sender availability
    helper = _get_notify_helper()
    if helper:
        logger.info("GitlabNotifyHelper found: %s (per-PR grouping enabled)", helper)
    else:
        logger.warning(
            "GitlabNotifyHelper not found — notifications won't be clickable or grouped. "
            "Run install.sh to build it."
        )

    # Read pre-fetched email data
    try:
        with open(input_path, encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        logger.error("Failed to read input file %s: %s", input_path, e)
        raw = ""

    if not raw.strip():
        logger.info("No new GitLab emails in '%s' mailbox. Done.", GITLAB_MAILBOX)
        save_state({"notified_ids": list(notified_ids), "last_run": None})
        # Write empty output
        with open(output_path, "w") as f:
            pass
        return

    # Parse
    emails = parse_emails(raw, logger)
    if not emails:
        logger.info("No parseable emails. Done.")
        save_state({"notified_ids": list(notified_ids), "last_run": None})
        with open(output_path, "w") as f:
            pass
        return

    # Process each email
    new_notifications = 0
    skipped_already_notified = 0
    skipped_unclassified = 0
    ids_to_move: list[str] = []

    for email in emails:
        msg_id = email.get("id", "")

        if msg_id in notified_ids:
            skipped_already_notified += 1
            logger.debug("Already notified, skipping: %s", email.get("subject", "?"))
            continue

        classification = classify_email(email, logger)
        if classification is None:
            skipped_unclassified += 1
            logger.debug("Unclassified, skipping: %s", email.get("subject", "?"))
            notified_ids.add(msg_id)
            ids_to_move.append(msg_id)
            continue

        url = extract_pr_url(email, logger)
        logger.info(
            "NOTIFY [%s] %s — %s | %s (url=%s)",
            classification.type,
            classification.title,
            classification.project,
            classification.pr_title,
            url or "none",
        )

        # Send notification (with per-type icon if available)
        icon_path = SCRIPT_DIR / "icons" / f"{classification.title}.png"
        send_notification(
            classification.notify_title,
            classification.notify_body,
            url=url,
            group_id=classification.group_id,
            notification_id=classification.notification_id,
            image=str(icon_path) if icon_path.exists() else None,
            logger=logger,
        )

        notified_ids.add(msg_id)
        ids_to_move.append(msg_id)
        new_notifications += 1

    # Write IDs for the wrapper AppleScript to mark-as-read / move
    with open(output_path, "w") as f:
        for mid in ids_to_move:
            f.write(mid + "\n")

    # Summary
    logger.info(
        "Run complete: %d new notification(s), %d already notified, %d unclassified, %d to move",
        new_notifications,
        skipped_already_notified,
        skipped_unclassified,
        len(ids_to_move),
    )

    # Save state
    save_state({"notified_ids": list(notified_ids)})
    logger.info("State saved. Done.")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--process":
        process_from_file(sys.argv[2], sys.argv[3])
    else:
        main()
