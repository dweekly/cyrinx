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
    // Toolchain (the JDK that actually RUNS the Kotlin/Java compilers) stays
    // pinned to 21, matching this repo's gradle/gradle-daemon-jvm.properties
    // (toolchainVersion=21, copied from Apps/HIL/android) so no extra JDK needs to
    // be provisioned locally for the compiler itself.
    jvmToolchain(21)
    compilerOptions {
        // Emitted BYTECODE version is 17, not 21: the C3-30 Android app
        // (Apps/HIL/android's own app module, for reference, targets JVM 17
        // because that is what its Android compileSdk requires) is an
        // Android-baseline JVM-17 consumer, and it must be able to depend on and
        // link against this module's compiled classes. Kotlin's `jvmTarget`
        // governs the actual class-file version this module's own compilation
        // produces, independent of which JDK (21) ran the compiler.
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// The `java` extension (auto-applied by kotlin("jvm")) publishes this module's
// `org.gradle.jvm.version` Gradle variant attribute from `targetCompatibility` --
// that attribute, not the Kotlin compiler's own `jvmTarget` setting above, is
// what Gradle's dependency resolution checks when a JVM-17-toolchain consumer
// (:consumer-compile-check here; the real C3-30 Android app later) depends on
// this module. Leaving it at the JDK 21 toolchain default would make Gradle
// refuse that dependency ("Dependency requires at least JVM runtime version
// 21") even though the actual class files are already JVM-17-encoded per the
// `jvmTarget` setting above.
java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

// Belt-and-suspenders alongside `java.targetCompatibility` above: pins the
// `javac --release 17` behavior directly on every JavaCompile task (there are
// no actual .java sources in this Kotlin-only module, so these tasks compile
// nothing today, but this keeps the module's declared Java compatibility
// unambiguous -- both "kotlin compilerOptions jvmTarget 17" and "java release
// 17" hold simultaneously, matching how Kotlin Gradle Plugin cross-checks the
// two -- rather than silently inheriting the JDK 21 toolchain default).
tasks.withType<JavaCompile>().configureEach {
    options.release.set(17)
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
