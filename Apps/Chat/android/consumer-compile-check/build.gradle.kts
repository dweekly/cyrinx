import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// C3-28 PR-review fix: a minimal Android-baseline-JVM-toolchain consumer of
// :chatkit's public API, proving the JVM-17-bytecode / variant-metadata split
// configured in ../chatkit/build.gradle.kts actually lets a JVM-17-toolchain
// consumer link against :chatkit without a Gradle "Dependency requires at
// least JVM runtime version 21" resolution failure. This module compiles one
// source file that touches ChatEnvelopeCodec and SimulatedChatPair (see
// src/main/kotlin/.../ConsumerCompileCheck.kt) and is wired into the `check`
// task below, so `./gradlew check` is the actual regression net.
//
// A true AGP (Android Gradle Plugin) consumer check -- exercising the real
// Android build/link path, not just plain-JVM Gradle variant resolution --
// arrives with the C3-30 app; this module is the plain-JVM stand-in until
// then.
plugins {
    kotlin("jvm")
}

kotlin {
    // The consumer side of the check: THIS module's toolchain is pinned to 17
    // (not 21, unlike :chatkit's own build toolchain -- see
    // ../chatkit/build.gradle.kts) to model an Android-baseline JVM-17
    // consumer. If :chatkit's published `org.gradle.jvm.version` variant
    // attribute were ever left at 21 (e.g. a future edit to
    // ../chatkit/build.gradle.kts drops its `java.targetCompatibility`
    // override), resolving the dependency below would fail with a Gradle
    // variant-compatibility error -- exactly the regression this module exists
    // to catch.
    jvmToolchain(17)
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(project(":chatkit"))
    // Only needed because the touched API (SimulatedChatPair.create) takes a
    // CoroutineScope; version pin matches chatkit/build.gradle.kts's own pin
    // (see that file's comment for the version-pin-checking convention this
    // repo follows).
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")
}

// `check` normally depends on `test`, and this module intentionally has no
// test source set (it exists purely to prove compilation/linking against
// :chatkit succeeds under a JVM-17 toolchain, not to test behavior chatkit's
// own test suite already covers). Depending on `compileKotlin` explicitly
// guarantees `check` always forces the actual compile-against-:chatkit step
// this module exists for, rather than relying on the "test task with no test
// sources still happens to trigger compilation" nuance of a given Gradle/JUnit
// version.
tasks.named("check") {
    dependsOn("compileKotlin")
}
