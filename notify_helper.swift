// ============================================================
// GitLab Notify Helper — Swift notification sender
// ============================================================
// A minimal macOS .app that sends notifications using
// UNUserNotificationCenter, which supports `threadIdentifier`
// for proper per-PR grouping in Notification Center.
//
// Usage:
//   GitlabNotifyHelper.app/Contents/MacOS/GitlabNotifyHelper \
//     -title "Review Requested" \
//     -message "cav-ts-apps-tools !942: feat: semantic releases" \
//     -open "https://gitlab.com/..." \
//     -group "gitlab-cav-ts-apps-tools-!942" \
//     -identifier "review_requested-gitlab-cav-ts-apps-tools-!942" \
//     -image "/path/to/icons/Review Requested.png"
//
// Arguments:
//   -title         Notification title
//   -subtitle      Secondary text line (shown between title and body)
//   -message       Notification body text (supports \n for newlines)
//   -open          URL to open when the notification is clicked
//   -group         Thread identifier for visual grouping in Notification Center
//   -identifier    Notification ID for replacement (same ID = replace old)
//                  If omitted, defaults to -group value
//   -image         Path to a PNG icon to display as a notification attachment
//                  (copied to a temp location because macOS moves non-bundled
//                  attachments, which would delete the original)
//
// Build (done by install.sh):
//   swiftc -O -o GitlabNotifyHelper notify_helper.swift \
//     -framework Cocoa -framework UserNotifications
// ============================================================

import Cocoa
import UserNotifications

// MARK: - Argument parsing

struct NotifyArgs {
    var title: String = ""
    var subtitle: String = ""
    var message: String = ""
    var openURL: String?
    var group: String = "gitlab"
    var identifier: String?
    var imagePath: String?
}

func parseArgs() -> NotifyArgs {
    var args = NotifyArgs()
    let argv = CommandLine.arguments
    var i = 1
    while i < argv.count {
        switch argv[i] {
        case "-title":
            i += 1; if i < argv.count { args.title = argv[i] }
        case "-subtitle":
            i += 1; if i < argv.count { args.subtitle = argv[i] }
        case "-message":
            i += 1; if i < argv.count { args.message = argv[i] }
        case "-open":
            i += 1; if i < argv.count { args.openURL = argv[i] }
        case "-group":
            i += 1; if i < argv.count { args.group = argv[i] }
        case "-identifier":
            i += 1; if i < argv.count { args.identifier = argv[i] }
        case "-image":
            i += 1; if i < argv.count { args.imagePath = argv[i] }
        default:
            // Silently skip unknown flags (and consume their value)
            if argv[i].hasPrefix("-") && i + 1 < argv.count
                && !argv[i + 1].hasPrefix("-") {
                i += 1
            }
        }
        i += 1
    }
    // Default identifier to group if not specified
    if args.identifier == nil {
        args.identifier = args.group
    }
    return args
}

// MARK: - App delegate (handles notification lifecycle)

class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {

    private var notifyArgs = NotifyArgs()

    func applicationDidFinishLaunching(_ notification: Notification) {
        notifyArgs = parseArgs()

        let center = UNUserNotificationCenter.current()
        center.delegate = self

        center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            guard granted else {
                fputs("Notification permission not granted\n", stderr)
                if let error = error {
                    fputs("  Error: \(error.localizedDescription)\n", stderr)
                }
                DispatchQueue.main.async { NSApp.terminate(nil) }
                return
            }
            DispatchQueue.main.async {
                self.sendNotification()
            }
        }
    }

    private func sendNotification() {
        let content = UNMutableNotificationContent()
        content.title = notifyArgs.title
        content.subtitle = notifyArgs.subtitle
        content.body = notifyArgs.message
        content.sound = .default

        // threadIdentifier → visual grouping in Notification Center
        content.threadIdentifier = notifyArgs.group

        // Store the URL so we can open it on click
        if let url = notifyArgs.openURL {
            content.userInfo = ["url": url]
        }

        // Attach per-notification icon (if provided)
        if let imagePath = notifyArgs.imagePath {
            let srcURL = URL(fileURLWithPath: imagePath)
            let fileManager = FileManager.default
            if fileManager.fileExists(atPath: imagePath) {
                do {
                    // Copy to temp dir because UNNotificationAttachment MOVES
                    // files that are outside the app bundle.
                    let tmpDir = fileManager.temporaryDirectory
                        .appendingPathComponent("gitlab-notify-icons", isDirectory: true)
                    try fileManager.createDirectory(at: tmpDir, withIntermediateDirectories: true)
                    let tmpFile = tmpDir.appendingPathComponent(srcURL.lastPathComponent)
                    // Remove stale temp copy if it exists from a previous run
                    try? fileManager.removeItem(at: tmpFile)
                    try fileManager.copyItem(at: srcURL, to: tmpFile)
                    let attachment = try UNNotificationAttachment(
                        identifier: "icon",
                        url: tmpFile,
                        options: [UNNotificationAttachmentOptionsTypeHintKey: "public.png"]
                    )
                    content.attachments = [attachment]
                } catch {
                    fputs("Failed to attach image: \(error.localizedDescription)\n", stderr)
                }
            }
        }

        // identifier → replacement (same ID = update existing notification)
        let requestID = notifyArgs.identifier ?? notifyArgs.group
        let request = UNNotificationRequest(
            identifier: requestID,
            content: content,
            trigger: nil  // deliver immediately
        )

        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                fputs("Failed to post notification: \(error.localizedDescription)\n", stderr)
            }
            // Auto-exit after a short delay.  We stay alive briefly so
            // the delegate can handle an immediate click, but we don't
            // linger — the notifier runs every 30 s anyway.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                NSApp.terminate(nil)
            }
        }
    }

    // Show banner even if this app is "in foreground" (it always is,
    // briefly, while delivering the notification).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        if #available(macOS 12.0, *) {
            completionHandler([.banner, .sound])
        } else {
            completionHandler([.alert, .sound])
        }
    }

    // Handle notification click → open URL
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
           let urlString = response.notification.request.content
               .userInfo["url"] as? String,
           let url = URL(string: urlString) {
            NSWorkspace.shared.open(url)
        }
        completionHandler()
        NSApp.terminate(nil)
    }
}

// MARK: - Entry point

let app = NSApplication.shared
// LSUIElement is set in Info.plist, but also enforce programmatically
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
