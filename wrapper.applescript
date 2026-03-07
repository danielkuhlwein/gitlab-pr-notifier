-- GitLab Notifier — AppleScript Wrapper
-- =======================================
-- This script is compiled into GitlabNotifier.app via osacompile.
-- It handles ALL Mail.app interaction directly (fetch, mark-read, move,
-- cleanup) so that macOS TCC grants the Automation permission to THIS
-- app — not to a python3 subprocess.
--
-- Python (gitlab_notifier.py --process) handles classification,
-- notifications, and state management only.
--
-- Paths below are substituted by install.sh before compilation.

set pythonBin to "__PYTHON3_PATH__"
set notifierScript to "__NOTIFIER_SCRIPT__"
set gitlabSender to "gitlab@mg.gitlab.com"
set lookbackHours to 2
set gitlabMailbox to "Gitlab"
set ttlHours to 24

set tempIn to "/tmp/gitlab_notifier_in.txt"
set tempOut to "/tmp/gitlab_notifier_out.txt"

-- ================================================================
-- Logging helper — uses native AppleScript file I/O
-- ================================================================
-- Log file sits alongside the main Python logs for easy tailing.
set logDir to "__LOG_DIR__"
set logFile to logDir & "/wrapper.log"

-- Timestamped logger using native AppleScript file I/O.
-- Previous version used "do shell script echo >>" which silently
-- failed inside try blocks, producing zero diagnostic output.
on tsLog(logFile, msg)
	try
		-- Get timestamp via a quick shell call
		set ts to do shell script "date '+%Y-%m-%d %H:%M:%S'"
		set logLine to "[" & ts & "] " & msg & "
"
		set fileRef to open for access POSIX file logFile with write permission
		write logLine to fileRef starting at eof as «class utf8»
		close access fileRef
	on error errMsg
		-- Last-resort: try to close the file handle if open
		try
			close access POSIX file logFile
		end try
		-- If even logging fails, write to a known fallback location
		try
			set fallback to "/tmp/gitlab_notifier_wrapper_error.log"
			set errLine to "[LOGERR] " & errMsg & " | original msg: " & msg & "
"
			set fb to open for access POSIX file fallback with write permission
			write errLine to fb starting at eof as «class utf8»
			close access fb
		end try
	end try
end tsLog

my tsLog(logFile, "===========================================")
my tsLog(logFile, "=== Wrapper AppleScript starting (PID " & (do shell script "echo $$") & ") ===")
my tsLog(logFile, "pythonBin: " & pythonBin)
my tsLog(logFile, "notifierScript: " & notifierScript)

-- ================================================================
-- 1. Fetch GitLab emails from the Gitlab mailbox
-- ================================================================
-- A Mail.app rule auto-moves and marks-as-read all emails from the
-- GitLab sender into the "Gitlab" mailbox.  We query ALL recent
-- messages there (not just unread) and rely on the Python state file
-- (notified_ids) to skip already-processed ones.
my tsLog(logFile, "Step 1: Fetching GitLab emails from '" & gitlabMailbox & "' mailbox...")

set emailData to ""
set emailCount to 0
try
	tell application "Mail"
		set lookbackDate to (current date) - (lookbackHours * hours)
		my tsLog(logFile, "  lookbackDate: " & (lookbackDate as string))
		my tsLog(logFile, "  currentDate:  " & ((current date) as string))

		-- Reference the Gitlab mailbox (populated by Mail.app rule)
		set targetBox to mailbox gitlabMailbox
		set totalInBox to count of messages of targetBox
		my tsLog(logFile, "  Total messages in '" & gitlabMailbox & "' mailbox: " & (totalInBox as string))

		-- Query all recent emails from GitLab sender (read status irrelevant)
		set gitlabMessages to (messages of targetBox whose ¬
			sender contains gitlabSender and ¬
			date received > lookbackDate)

		set emailCount to count of gitlabMessages
		my tsLog(logFile, "  Found " & (emailCount as string) & " recent GitLab email(s)")

		set output to ""
		repeat with msg in gitlabMessages
			try
				set msgId to id of msg as string
				set msgSubject to subject of msg
				set msgSender to sender of msg
				set msgDate to date received of msg as string
				set msgSource to source of msg

				my tsLog(logFile, "  Email: id=" & msgId & " subject=" & msgSubject)

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

		set emailData to output
	end tell
	my tsLog(logFile, "Step 1 complete: fetched " & (emailCount as string) & " email(s)")
on error errMsg number errNum
	my tsLog(logFile, "Step 1 FAILED: " & errMsg & " (error " & (errNum as string) & ")")
	set emailData to ""
end try

-- ================================================================
-- 2. Write email data to temp file
-- ================================================================
my tsLog(logFile, "Step 2: Writing email data to " & tempIn)
try
	set fileRef to open for access POSIX file tempIn with write permission
	set eof of fileRef to 0
	write emailData to fileRef as «class utf8»
	close access fileRef
	set fileSize to do shell script "wc -c < " & quoted form of tempIn
	my tsLog(logFile, "Step 2 complete: wrote " & fileSize & " bytes")
on error errMsg number errNum
	my tsLog(logFile, "Step 2 FAILED: " & errMsg & " (error " & (errNum as string) & ")")
	try
		close access POSIX file tempIn
	end try
end try

-- ================================================================
-- 3. Run Python for classification, notifications, and state mgmt.
-- ================================================================
my tsLog(logFile, "Step 3: Running Python --process mode...")
set pythonCmd to pythonBin & " " & quoted form of notifierScript & " --process " & quoted form of tempIn & " " & quoted form of tempOut
my tsLog(logFile, "  cmd: " & pythonCmd)

try
	set pythonOutput to do shell script pythonCmd & " 2>&1"
	my tsLog(logFile, "Step 3 complete (exit 0)")
	if pythonOutput is not "" then
		-- Log first portion of Python output (AppleScript has no min(), so use conditional)
		set maxChars to 500
		set outputLen to length of pythonOutput
		if outputLen < maxChars then set maxChars to outputLen
		my tsLog(logFile, "  Python stdout (" & (outputLen as string) & " chars, showing first " & (maxChars as string) & "): " & (text 1 thru maxChars of pythonOutput))
	end if
on error errMsg number errNum
	my tsLog(logFile, "Step 3 FAILED: " & errMsg & " (error " & (errNum as string) & ")")
end try

-- ================================================================
-- 4. Read IDs to mark as read + move from Python's output
-- ================================================================
my tsLog(logFile, "Step 4: Reading IDs from " & tempOut)
set idsToMark to {}
try
	set idsText to do shell script "cat " & quoted form of tempOut & " 2>/dev/null || true"
	if idsText is not "" then
		set AppleScript's text item delimiters to return
		set idsToMark to text items of idsText
		set AppleScript's text item delimiters to ""
	end if
	my tsLog(logFile, "Step 4 complete: " & ((count of idsToMark) as string) & " ID(s) to mark/move")
on error errMsg number errNum
	my tsLog(logFile, "Step 4 FAILED: " & errMsg & " (error " & (errNum as string) & ")")
end try

-- ================================================================
-- 5-6. Mark-as-read and move — handled by Mail.app rule
-- ================================================================
-- A Mail.app rule automatically marks GitLab emails as read and moves
-- them to the Gitlab mailbox on arrival.  Steps 5-6 are no longer needed.
my tsLog(logFile, "Steps 5-6: Skipped (Mail.app rule handles mark-read/move)")
my tsLog(logFile, "  IDs processed by Python: " & ((count of idsToMark) as string))

-- ================================================================
-- 7. Clean up old emails in the Gitlab mailbox (TTL)
-- ================================================================
my tsLog(logFile, "Step 7: Cleaning up emails older than " & (ttlHours as string) & "h in Gitlab mailbox...")
try
	set deletedCount to 0
	tell application "Mail"
		set targetBox to mailbox gitlabMailbox
		set cutoffDate to (current date) - (ttlHours * hours)
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
	end tell
	my tsLog(logFile, "Step 7 complete: deleted " & (deletedCount as string) & " old email(s)")
on error errMsg number errNum
	my tsLog(logFile, "Step 7 FAILED: " & errMsg & " (error " & (errNum as string) & ")")
end try

-- ================================================================
-- 8. Clean up temp files
-- ================================================================
my tsLog(logFile, "Step 8: Cleaning up temp files...")
try
	do shell script "rm -f " & quoted form of tempIn & " " & quoted form of tempOut
	my tsLog(logFile, "Step 8 complete")
end try

my tsLog(logFile, "=== Wrapper AppleScript finished ===")
my tsLog(logFile, "")
