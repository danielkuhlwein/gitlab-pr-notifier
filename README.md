<div align="center">
  <h1>GitLab PR Notifier</h1>
  <p><strong>This project has been superseded by <a href="https://github.com/danielkuhlwein/tanuki-bell">Tanuki Bell</a></strong></p>
</div>

<br>

> [!IMPORTANT]
> **This repository is deprecated.** It has been replaced by [**Tanuki Bell**](https://github.com/danielkuhlwein/tanuki-bell) — a native macOS menu bar app built in Swift/SwiftUI that connects to the GitLab API directly. No Mail.app dependency, no LaunchAgents, no Terminal setup required.

## Why the switch?

This project worked by polling Apple Mail for GitLab email notifications — a multi-component pipeline involving AppleScript, Python, Swift, and a LaunchAgent plist. It got the job done, but had inherent limitations:

- Depended on Mail.app receiving GitLab emails (extra latency, deliverability issues)
- Required a manually-created LaunchAgent plist due to macOS `com.apple.provenance` restrictions
- Multiple compiled artifacts and shell scripts to coordinate
- No UI — configuration required editing Python variables

[**Tanuki Bell**](https://github.com/danielkuhlwein/tanuki-bell) replaces all of this with a single native SwiftUI app that talks to the GitLab API directly, supports 14 notification types, includes a menu bar popover with notification history, per-type notification preferences, and auto-updates via Sparkle.

## License

[MIT](LICENSE)
