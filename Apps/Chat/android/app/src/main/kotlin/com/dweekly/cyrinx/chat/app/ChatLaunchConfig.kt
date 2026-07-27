package com.dweekly.cyrinx.chat.app

import android.content.Intent
import android.os.Bundle
import com.dweekly.cyrinx.chat.ChatLaunchArgs
import com.dweekly.cyrinx.chat.ChatScenario

/**
 * Parsed `chat.scenario`/`chat.seed`/`chat.simulated` launch configuration --
 * CONTRACT.md section 5: "Android: instrumentation args / `Intent` extras with
 * the identical keys." [ChatLaunchArgs] supplies the three key strings shared
 * verbatim with Swift's launch-argument names.
 *
 * [fromArgs] is the plain-Kotlin, JVM-testable core (takes a `(String) ->
 * String?` lookup function, so it needs no `android.os.Bundle`/`Intent`
 * instance -- both of which throw "not mocked" under a plain JVM unit test
 * without Robolectric). [fromIntent]/[fromInstrumentationArgs] are thin
 * Android-only adapters around it, exercised only by the app at runtime /
 * instrumentation tests, not by this module's JVM gate.
 */
data class ChatLaunchConfig(
    val scenario: ChatScenario,
    val seed: Long,
    val simulated: Boolean,
) {
    companion object {
        /** `chat.scenario` defaults to `happyPair`, `chat.seed` to `1`, and
         * `chat.simulated` defaults `true` per CONTRACT.md section 5's own
         * "`chat.simulated`: Bool, default true." DECISION (not pinned by the
         * brief): CONTRACT.md does not pin a default scenario/seed for when
         * the launch arguments are absent entirely (e.g. a plain launcher tap,
         * not `am start -e ...`); `happyPair`/`1` are chosen as the smallest,
         * fully-successful demonstration scenario so a cold launcher tap shows
         * a working conversation rather than an error state. */
        val DEFAULT: ChatLaunchConfig = ChatLaunchConfig(ChatScenario.HAPPY_PAIR, seed = 1L, simulated = true)

        fun fromArgs(get: (key: String) -> String?): ChatLaunchConfig {
            val scenario = get(ChatLaunchArgs.SCENARIO)?.let { ChatScenario.fromWireName(it) } ?: DEFAULT.scenario
            // CONTRACT.md section 5: "chat.seed: UInt64 decimal." Parsed via
            // ULong so the full unsigned 64-bit range round-trips (a seed at
            // or above 2^63 would silently fail/clip under a plain
            // Long.toLongOrNull() on some inputs); reinterpreted as a raw Long
            // bit pattern for SplitMix64, which only ever does unsigned/XOR
            // arithmetic on it (CONTRACT.md section 2).
            val seed = get(ChatLaunchArgs.SEED)?.toULongOrNull()?.toLong() ?: DEFAULT.seed
            val simulated = get(ChatLaunchArgs.SIMULATED)?.toBooleanStrictOrNull() ?: DEFAULT.simulated
            return ChatLaunchConfig(scenario, seed, simulated)
        }

        /** Android launch path: `am start -e chat.scenario happyPair ...`
         * always delivers `-e` extras as `String`s, so a plain
         * `Bundle.getString` lookup is sufficient (CONTRACT.md section 5's
         * example is exactly `-e`-flavored). */
        fun fromIntent(intent: Intent?): ChatLaunchConfig = fromArgs { key -> intent?.extras?.getString(key) }

        /** Instrumentation path: `am instrument ... -e chat.scenario
         * happyPair`, read via `androidx.test.platform.app
         * .InstrumentationRegistry.getArguments()`. Takes the already-fetched
         * [Bundle] rather than calling `InstrumentationRegistry` itself so
         * this file has no `androidx.test` dependency (that artifact is
         * `androidTestImplementation`-only; see build.gradle.kts). */
        fun fromInstrumentationArgs(args: Bundle?): ChatLaunchConfig = fromArgs { key -> args?.getString(key) }
    }
}
