import CyrinxChatKit
import SwiftUI

/// The coarse directional link-budget badge (design brief: "link-budget
/// badge"), backed by the numeric `ChatLinkBudget` (CONTRACT.md §1.4) but
/// only ever showing its coarse `classification` here -- the sample's
/// diagnostics sheet is where a future stage could surface the numeric
/// bounds/confidence/age. An SF Symbol plus a plain-language label both
/// carry the meaning, matching every other status surface in this sample.
struct LinkBudgetBadgeView: View {
    let budget: ChatLinkBudget

    var body: some View {
        Label(classificationLabel, systemImage: symbolName)
            .font(.caption)
            .fontWeight(.medium)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.secondary.opacity(0.15), in: Capsule())
            .accessibilityIdentifier(ChatAccessibilityID.linkBudgetBadge)
            .accessibilityLabel("Link quality: \(classificationLabel)")
    }

    private var classificationLabel: String {
        switch budget.classification {
        case .controlOnly: return "Control only"
        case .text: return "Text"
        case .thumbnail: return "Thumbnail"
        case .bulk: return "Bulk"
        }
    }

    private var symbolName: String {
        switch budget.classification {
        case .controlOnly: return "antenna.radiowaves.left.and.right.slash"
        case .text: return "text.bubble"
        case .thumbnail: return "photo"
        case .bulk: return "shippingbox"
        }
    }
}

#Preview {
    LinkBudgetBadgeView(
        budget: ChatLinkBudget(
            classification: .text, txLowerBoundBps: nil, rxLowerBoundBps: nil, confidence: 0.7, ageMs: 0
        )
    )
}
