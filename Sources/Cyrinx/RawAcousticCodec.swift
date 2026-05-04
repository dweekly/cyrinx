import Foundation

public enum RawAcousticCodec: String, Sendable {
    case auto
    case basic
    case reverseBurst = "reverse-burst"
    case ook
    case morse
    case dtmf
    case nibble

    static func resolved(_ requested: RawAcousticCodec, config: Config) -> RawAcousticCodec {
        if requested != .auto {
            return requested
        }
        if config.bandStartHz == config.bandEndHz {
            return .ook
        }
        if config.role == .slave {
            return .reverseBurst
        }
        return .basic
    }
}
