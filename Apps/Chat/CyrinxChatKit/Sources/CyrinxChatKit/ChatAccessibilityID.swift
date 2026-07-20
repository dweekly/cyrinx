/// Accessibility identifiers for the chat sample's UI (landing in
/// C3-29/C3-30). String values shared verbatim (byte-identical) with
/// Kotlin's `ChatTestTags` object -- Apps/Chat/CONTRACT.md §5's registry
/// table, which is the authority for both the constant names and their
/// string values. `Apps/Chat/android/chatkit/src/main/kotlin/com/dweekly/
/// cyrinx/chat/ChatTestTags.kt` is the Kotlin twin; names here are kept
/// parallel to it (`peerList` <-> `PEER_LIST`, etc.) modulo each language's
/// own naming convention.
///
/// A non-instantiable namespace enum (no cases), the idiomatic Swift
/// equivalent of Kotlin's `object` -- CONTRACT.md §5 itself names this
/// exact shape: "Swift: `enum ChatAccessibilityID`. Kotlin: `object
/// ChatTestTags`."
public enum ChatAccessibilityID {
    public static let peerList = "chat.peerList"
    public static let peerRow = "chat.peerRow"
    public static let connectButton = "chat.connectButton"
    public static let connectionBanner = "chat.connectionBanner"
    public static let linkBudgetBadge = "chat.linkBudgetBadge"
    public static let messageList = "chat.messageList"
    public static let messageRow = "chat.messageRow"
    public static let messageStatus = "chat.messageStatus"
    public static let composerField = "chat.composerField"
    public static let sendButton = "chat.sendButton"
    public static let diagnosticsButton = "chat.diagnosticsButton"
    public static let unauthenticatedNotice = "chat.unauthenticatedNotice"
    public static let errorBanner = "chat.errorBanner"
}

/// Launch/instrumentation argument keys. Apps/Chat/CONTRACT.md §5. Apple:
/// process launch arguments, e.g. `-chat.scenario happyPair`. Mirrors
/// Kotlin's `ChatLaunchArgs` object (same source file as `ChatTestTags` on
/// that platform), which uses instrumentation args / `Intent` extras with
/// these identical keys, e.g. `am instrument ... -e chat.scenario
/// happyPair`.
public enum ChatLaunchArgs {
    /// `String`, one of the six scenario names in CONTRACT.md §3.
    public static let scenario = "chat.scenario"
    /// `UInt64` decimal seed for the scenario's PRNG (CONTRACT.md §2).
    public static let seed = "chat.seed"
    /// `Bool`, default `true`. `false` selects the live SDK adapter -- not
    /// available until C3-31; default stays `true` through C3-28-C3-30.
    public static let simulated = "chat.simulated"
}
