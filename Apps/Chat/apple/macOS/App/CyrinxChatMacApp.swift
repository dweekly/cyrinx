import SwiftUI

@main
struct CyrinxChatMacApp: App {
    var body: some Scene {
        WindowGroup {
            ChatRootView()
        }
        .defaultSize(width: 420, height: 640)
    }
}
