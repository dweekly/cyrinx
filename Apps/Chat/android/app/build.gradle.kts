import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// C3-30: the Android offline chat sample application. Compose Material3 UI over
// the C3-28 :chatkit contract/simulated-transport module (read-only dependency --
// see ../../CONTRACT.md and ../../README.md's "Simulator-first development").
// AGP/SDK conventions (compileSdk/minSdk/targetSdk, JVM target) match
// ../../../HIL/android/app/build.gradle.kts exactly; see this module's own
// build.gradle.kts for the version-currency citations (AGP release notes,
// Compose Compiler Gradle plugin docs).
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.dweekly.cyrinx.chat.app"
    // Matches Apps/HIL/android/app/build.gradle.kts's compileSdk/targetSdk (37) --
    // AGP 9.2's max supported compileSdk per the release notes cited in the root
    // build.gradle.kts. minSdk also matches HIL's own baseline (26).
    compileSdk = 37

    defaultConfig {
        applicationId = "com.dweekly.cyrinx.chat.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"

        // CONTRACT.md section 5's launch-argument keys are read from Intent
        // extras / instrumentation args at runtime (see ChatLaunchConfig.kt), not
        // as a build-time manifest placeholder -- this just names the
        // instrumentation runner instrumentation tests drive via
        // `am instrument ... -e chat.scenario happyPair`.
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    // JVM unit tests (:app:testDebugUnitTest) are plain-JVM tests of pure Kotlin
    // logic (ChatProjection, ChatViewModel wired to a SimulatedChatPair running
    // under kotlinx-coroutines-test virtual time) -- none of this module's tested
    // code touches an actual android.* framework class, so the AGP default
    // (unmocked android.* calls throw) stays on rather than silently returning
    // stub zero/null values, which would mask an accidental framework dependency
    // creeping into tested code.
    testOptions {
        unitTests {
            isIncludeAndroidResources = false
            isReturnDefaultValues = false
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // The C3-28 transport contract + simulated client -- read-only dependency,
    // see ../../CONTRACT.md and ../../README.md.
    implementation(project(":chatkit"))

    // Version pins checked current against
    // https://dl.google.com/dl/android/maven2/.../maven-metadata.xml for each
    // artifact (per AGENTS.md's "use latest available versions, verify, don't
    // trust recall" convention) at the time this module was created.
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")

    val composeBom = platform("androidx.compose:compose-bom:2026.06.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // JVM (:app:testDebugUnitTest) -- virtual-time scenario/projection tests, no
    // Android framework or emulator involved. Matches chatkit's own JUnit4 +
    // kotlinx-coroutines-test flavor (../../chatkit/build.gradle.kts) and
    // Apps/HIL/android/app/build.gradle.kts's JUnit 4 pin.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")

    // Instrumentation (:app:connectedDebugAndroidTest) -- documented emulator-
    // required; not part of this module's CI/local JVM gates. See
    // src/androidTest/.../ChatFlowsInstrumentedTest.kt's class doc comment.
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

tasks.withType<Test>().configureEach {
    useJUnit()
    testLogging {
        events("passed", "skipped", "failed")
    }
    // Same convention as ../../chatkit/build.gradle.kts: the model-trace golden
    // fixtures this module's tests read (ChatModelTraceGoldenComparisonTest) are
    // specified relative to Apps/Chat/android/ (this build's rootDir), not this
    // subproject's own directory (Apps/Chat/android/app/), which is Gradle's
    // default Test.workingDir.
    workingDir = rootDir
}
