plugins {
    // Version pin checked against https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-gradle-plugin/
    // (latest non-beta release at the time this module was created) per AGENTS.md's
    // "don't hardcode arbitrary package versions, use latest available" convention.
    kotlin("jvm") version "2.4.10" apply false

    // C3-30: the :app module (Compose Material3 Android application). AGP version
    // matches Apps/HIL/android/build.gradle.kts's own pin exactly (checked current
    // against https://developer.android.com/build/releases/agp-9-2-0-release-notes,
    // April 2026, max supported compileSdk 37 -- matches this module's compileSdk
    // below). AGP 9.0+ provides Kotlin compilation BUILT IN -- applying the old
    // separate "org.jetbrains.kotlin.android" plugin is now a hard error
    // ("no longer required... since AGP 9.0", https://kotl.in/gradle/agp-built-in-kotlin),
    // discovered empirically when this module was first built (matches
    // Apps/HIL/android/gradle.properties's own `android.builtInKotlin=true` opt-in,
    // which is AGP 9's default behavior here, not a separately-set property in
    // this module). The Compose Compiler Gradle plugin is still a SEPARATE plugin
    // (required from Kotlin 2.0 onward now that the Compose compiler moved into
    // the Kotlin repository) -- version-pinned to the same 2.4.10 as "kotlin.jvm"
    // above (the Compose Compiler Gradle plugin's own versioning tracks Kotlin's
    // directly; checked current against
    // https://plugins.gradle.org/plugin/org.jetbrains.kotlin.plugin.compose).
    id("com.android.application") version "9.2.1" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.10" apply false
}
