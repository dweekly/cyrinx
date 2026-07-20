import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// Pure-JVM Kotlin module: org.jetbrains.kotlin.jvm only, no Android Gradle plugin,
// no android.* imports anywhere under src/. This is a C3-28 merge gate (see
// ../../README.md "Simulator-first development" / CONTRACT.md's non-goals): the
// chatkit contract, codec, and simulated transport must be exercisable from a plain
// JVM (e.g. a future CLI or non-Android host) without pulling in the Android SDK.
plugins {
    kotlin("jvm")
}

kotlin {
    // Pinned to 21 to match this repo's gradle/gradle-daemon-jvm.properties
    // (toolchainVersion=21, copied from Apps/HIL/android) so no extra JDK needs to be
    // provisioned locally; Apps/HIL/android's own app module targets JVM 17 only
    // because that is what Android's compileSdk requires there, a constraint that
    // does not apply to this Android-SDK-free module.
    jvmToolchain(21)
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_21)
    }
}

dependencies {
    // Version pins checked against
    // https://repo1.maven.org/maven2/org/jetbrains/kotlinx/kotlinx-coroutines-core/
    // and .../kotlinx-coroutines-test/ (latest stable releases at module creation
    // time) per AGENTS.md's "use latest available versions" convention.
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")

    // Matches Apps/HIL/android/app/build.gradle.kts's JUnit flavor exactly (JUnit 4).
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
}

tasks.test {
    useJUnit()
    testLogging {
        events("passed", "skipped", "failed")
    }
    // Gradle's default Test.workingDir is this subproject's own directory
    // (Apps/Chat/android/chatkit); the fixtures this suite reads
    // (../fixtures/chat-envelope-golden.json, ../fixtures/traces/) are specified
    // relative to Apps/Chat/android/ (this build's rootDir, one level up from
    // chatkit/), matching Apps/Chat/README.md's directory map. Pinning workingDir
    // here keeps every test's file-path arithmetic a single, auditable "../fixtures"
    // relative to a fixed, known root instead of depending on Gradle's default.
    workingDir = rootDir
}
