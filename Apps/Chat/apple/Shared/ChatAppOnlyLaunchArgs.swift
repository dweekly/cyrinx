import Foundation

/// App-only launch argument, deliberately NOT part of CONTRACT.md §5's
/// pinned registry (`ChatAccessibilityID`/`ChatLaunchArgs`, which are
/// shared byte-identical with Android): which side of the simulated pair
/// this app instance attaches its `ChatModel` to.
///
/// Exists because none of CONTRACT.md §3's six scenario scripts ever have
/// client A receive a message -- only client B does (A always originates
/// the scripted send). A single-app demo/UI-test flow that needs to show
/// an INCOMING message bubble (the "receive" flow in the C3-29 task
/// brief's "discovery/connect/send/receive/degraded/failed-message"
/// XCUITest list) must attach to B instead. Defaults to `"a"` -- the
/// natural "acting" role for an interactive single-user demo (the user
/// browses peers and initiates connect/send from A's point of view); a
/// UI test exercising the receive flow launches with `-chat.attachTo b`.
enum ChatAppOnlyLaunchArgs {
    static let attachTo = "chat.attachTo"

    /// Parses `-chat.attachTo a|b` from `arguments`, defaulting to `"a"`
    /// on anything else (missing, malformed, or an unrecognized value) --
    /// same "never make the app un-launchable" policy as
    /// `ChatLaunchArgParsing`.
    static func parseAttachToB(_ arguments: [String] = Array(CommandLine.arguments.dropFirst())) -> Bool {
        var iterator = arguments.makeIterator()
        while let arg = iterator.next() {
            guard arg == "-\(attachTo)", let value = iterator.next() else { continue }
            return value.lowercased() == "b"
        }
        return false
    }
}
