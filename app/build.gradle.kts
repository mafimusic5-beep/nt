import com.android.build.api.variant.FilterConfiguration.FilterType.ABI
import java.io.InputStream
import java.io.OutputStream
import java.net.URI
import java.util.zip.ZipInputStream
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
}

/** Must match [android.defaultConfig.versionCode] (used in [androidComponents] per-ABI overrides). */
val appVersionCode = 715
val skryonAllowedSignatureSha256s = (providers.gradleProperty("SKRYON_ALLOWED_SIGNATURE_SHA256S").orNull ?: "")
    .replace("\\", "\\\\")
    .replace("\"", "\\\"")
val skryonBlockTamperedRuntime = (providers.gradleProperty("SKRYON_BLOCK_TAMPERED_RUNTIME").orNull ?: "true")
    .lowercase()
    .let { value -> if (value == "false" || value == "0" || value == "no") "false" else "true" }

android {
    namespace = "com.v2ray.ang"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.skryon.shield"
        minSdk = 24
        targetSdk = 36
        versionCode = appVersionCode
        versionName = "2.0.15"
        multiDexEnabled = true

        // Public Skryon API endpoint. Nginx/Cloudflare proxy requests to the backend VPS.
        buildConfigField("String", "EMERY_API_BASE_URL", "\"https://skryon.ru\"")
        // Production release builds should be made with:
        // -PSKRYON_ALLOWED_SIGNATURE_SHA256S=<SHA256_OF_RELEASE_SIGNING_CERT>
        // Empty value keeps local/debug builds installable while still sending integrity headers.
        buildConfigField("String", "SKRYON_ALLOWED_SIGNATURE_SHA256S", "\"$skryonAllowedSignatureSha256s\"")
        buildConfigField("boolean", "SKRYON_BLOCK_TAMPERED_RUNTIME", skryonBlockTamperedRuntime)

        val abiFilterList = (properties["ABI_FILTERS"] as? String)?.split(';')
        splits {
            abi {
                isEnable = true
                reset()
                if (abiFilterList != null && abiFilterList.isNotEmpty()) {
                    include(*abiFilterList.toTypedArray())
                } else {
                    include(
                        "arm64-v8a",
                        "armeabi-v7a",
                        "x86_64",
                        "x86"
                    )
                }
                isUniversalApk = abiFilterList.isNullOrEmpty()
            }
        }

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            // Release APKs should be obfuscated so access checks and API paths are harder to patch out.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    flavorDimensions.add("distribution")
    productFlavors {
        create("fdroid") {
            dimension = "distribution"
            applicationIdSuffix = ".fdroid"
            buildConfigField("String", "DISTRIBUTION", "\"F-Droid\"")
        }
        create("playstore") {
            dimension = "distribution"
            buildConfigField("String", "DISTRIBUTION", "\"Play Store\"")
        }
    }

    sourceSets {
        getByName("main") {
            // Explicitly include both conventional and legacy prebuilt native folders.
            jniLibs.srcDirs(
                layout.projectDirectory.dir("src/main/jniLibs"),
                layout.projectDirectory.dir("libs"),
                "$buildDir/generated/hev-jniLibs",
            )
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
        compose = true
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

}

/**
 * AGP 9+: [applicationVariants] is not on the public [android] DSL. Per-output versionCode uses
 * [androidComponents] / [VariantOutput.versionCode]. Custom APK file names are not on [VariantOutput]
 * in AGP 8+; Gradle keeps default output names unless you add a rename task.
 */
androidComponents {
    onVariants { variant ->
        val isFdroid = variant.productFlavors.any { (_, flavor) -> flavor == "fdroid" }
        val fdroidAbiSuffix =
            mapOf(
                "armeabi-v7a" to 2,
                "arm64-v8a" to 1,
                "x86" to 4,
                "x86_64" to 3,
                "universal" to 0,
            )
        val playAbiMultiplier =
            mapOf(
                "armeabi-v7a" to 4,
                "arm64-v8a" to 4,
                "x86" to 4,
                "x86_64" to 4,
                "universal" to 4,
            )

        variant.outputs.forEach { output ->
            val abi = output.filters.find { it.filterType == ABI }?.identifier ?: "universal"

            // Per-output APK file names are not exposed on VariantOutput in AGP 8+; Gradle uses default output names.
            if (isFdroid) {
                val suffix = fdroidAbiSuffix[abi] ?: return@forEach
                output.versionCode.set((100 * appVersionCode + suffix) + 5_000_000)
            } else {
                val mult = playAbiMultiplier[abi] ?: return@forEach
                output.versionCode.set(1_000_000 * mult + appVersionCode)
            }
        }
    }
}

dependencies {
    // Core Libraries
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar", "*.jar"))))

    // AndroidX Core Libraries
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.preference.ktx)
    implementation(libs.recyclerview)
    implementation(libs.androidx.swiperefreshlayout)
    implementation(libs.androidx.viewpager2)
    implementation(libs.androidx.fragment)
    implementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.navigation.compose)
    debugImplementation(libs.androidx.compose.ui.tooling)

    // UI Libraries
    implementation(libs.material)
    implementation(libs.toasty)
    implementation(libs.editorkit)
    implementation(libs.flexbox)

    // Data and Storage Libraries
    implementation(libs.mmkv.static)
    implementation(libs.gson)
    implementation(libs.okhttp)

    // Reactive and Utility Libraries
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.core)

    // Language and Processing Libraries
    implementation(libs.language.base)
    implementation(libs.language.json)

    // Intent and Utility Libraries
    implementation(libs.quickie.foss)
    implementation(libs.core)

    // AndroidX Lifecycle and Architecture Components
    implementation(libs.lifecycle.viewmodel.ktx)
    implementation(libs.lifecycle.livedata.ktx)
    implementation(libs.lifecycle.runtime.ktx)

    // Background Task Libraries
    implementation(libs.work.runtime.ktx)
    implementation(libs.work.multiprocess)

    // Multidex Support
    implementation(libs.multidex)

    // Testing Libraries
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    testImplementation(libs.org.mockito.mockito.inline)
    testImplementation(libs.mockito.kotlin)
    coreLibraryDesugaring(libs.desugar.jdk.libs)
}

/**
 * Native bindings (`go.Seq`, `libv2ray.Libv2ray`, вЂ¦) ship inside libv2ray.aar.
 * Upstream CI downloads it from 2dust/AndroidLibXrayLite; local clones often omit the binary.
 * Override tag: ./gradlew assembleDebug -Plibv2ray.version=v26.3.9
 */
