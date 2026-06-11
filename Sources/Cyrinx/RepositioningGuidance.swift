import CCyrinx
import Foundation

/// A human-actionable hint for improving a struggling acoustic link, derived
/// purely from measured channel metrics (see `cyrinx_guidance.h`). A library
/// consumer renders these to coach its user toward a better arrangement.
public enum RepositioningHint: Sendable, Equatable {
    /// Link is healthy; no action needed.
    case ok
    /// Too far / too quiet — move the devices closer together.
    case moveCloser
    /// Reflective surface — use a soft surface or move away from hard walls.
    case softSurface
    /// Directional loss — point the phone's bottom edge at the speaker.
    case aimBottomEdge
    /// Clipping — lower the volume or move the devices slightly apart.
    case lowerVolume
    /// Ultrasonic uplink is phase-incoherent — switch to audible mode.
    case useAudible

    fileprivate init(_ c: cyrinx_reposition_hint) {
        switch c {
        case CYRINX_HINT_MOVE_CLOSER: self = .moveCloser
        case CYRINX_HINT_SOFT_SURFACE: self = .softSurface
        case CYRINX_HINT_AIM_BOTTOM_EDGE: self = .aimBottomEdge
        case CYRINX_HINT_LOWER_VOLUME: self = .lowerVolume
        case CYRINX_HINT_USE_AUDIBLE: self = .useAudible
        default: self = .ok
        }
    }

    fileprivate var cValue: cyrinx_reposition_hint {
        switch self {
        case .ok: return CYRINX_HINT_OK
        case .moveCloser: return CYRINX_HINT_MOVE_CLOSER
        case .softSurface: return CYRINX_HINT_SOFT_SURFACE
        case .aimBottomEdge: return CYRINX_HINT_AIM_BOTTOM_EDGE
        case .lowerVolume: return CYRINX_HINT_LOWER_VOLUME
        case .useAudible: return CYRINX_HINT_USE_AUDIBLE
        }
    }

    /// English default text (consumers may localize).
    public var text: String {
        String(cString: cyrinx_reposition_hint_text(cValue))
    }
}

/// Measured channel metrics fed to the guidance evaluator.
public struct ChannelMetrics: Sendable {
    public var medianSNRdB: Double
    public var rxPeak: Double
    public var delaySpreadMs: Double
    public var cyclicPrefixMs: Double
    public var highFreqRolloffDB: Double
    public var coherence: Double
    public var isUltrasonic: Bool

    public init(
        medianSNRdB: Double, rxPeak: Double, delaySpreadMs: Double,
        cyclicPrefixMs: Double, highFreqRolloffDB: Double = 0,
        coherence: Double = 1.0, isUltrasonic: Bool = false
    ) {
        self.medianSNRdB = medianSNRdB
        self.rxPeak = rxPeak
        self.delaySpreadMs = delaySpreadMs
        self.cyclicPrefixMs = cyclicPrefixMs
        self.highFreqRolloffDB = highFreqRolloffDB
        self.coherence = coherence
        self.isUltrasonic = isUltrasonic
    }
}

/// The chosen hint plus the severity (0 none, 1 advisory, 2 strong) and the
/// metric value that triggered it.
public struct RepositioningAdvice: Sendable {
    public let hint: RepositioningHint
    public let severity: Int
    public let evidence: Double
}

/// Evaluate channel metrics into the single highest-priority repositioning hint.
public func repositioningAdvice(for metrics: ChannelMetrics) -> RepositioningAdvice {
    var m = cyrinx_channel_metrics()
    m.median_snr_db = metrics.medianSNRdB
    m.rx_peak = metrics.rxPeak
    m.delay_spread_ms = metrics.delaySpreadMs
    m.cp_ms = metrics.cyclicPrefixMs
    m.hf_rolloff_db = metrics.highFreqRolloffDB
    m.coherence = metrics.coherence
    m.ultrasonic = metrics.isUltrasonic ? 1 : 0
    let a = cyrinx_repositioning_hint(&m)
    return RepositioningAdvice(
        hint: RepositioningHint(a.hint), severity: Int(a.severity), evidence: a.evidence_value)
}
