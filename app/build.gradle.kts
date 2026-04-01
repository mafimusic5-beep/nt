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

android {
    namespace = "com.v2ray.ang"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.v2ray.ang"
        minSdk = 24
        targetSdk = 36
        versionCode = appVersionCode
        versionName = "2.0.15"
        multiDexEnabled = true

        // Emery orchestrator API (override in device builds via your LAN IP, e.g. http://192.168.1.10:9330)
        buildConfigField("String", "EMERY_API_BASE_URL", "\"http://10.0.2.2:9330\"")

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
            isMinifyEnabled = false
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

            // Per-output APK file names are not exposed on VariantOutput in AGP 8+; Gradle uses default names.
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
 * Native bindings (`go.Seq`, `libv2ray.Libv2ray`, …) ship inside libv2ray.aar.
 * Upstream CI downloads it from 2dust/AndroidLibXrayLite; local clones often omit the binary.
 * Override tag: ./gradlew assembleDebug -Plibv2ray.version=v26.3.9
 */
val libv2rayAar = layout.projectDirectory.file("libs/libv2ray.aar").asFile
val libv2rayVersionProperty =
    (project.findProperty("libv2ray.version") as String?)?.trim()?.takeIf { it.isNotEmpty() } ?: "v26.3.9"

val downloadLibv2ray = tasks.register("downloadLibv2ray") {
    group = "setup"
    description = "Downloads libv2ray.aar from AndroidLibXrayLite if app/libs/libv2ray.aar is missing."
    onlyIf { !libv2rayAar.exists() }
    doLast {
        libv2rayAar.parentFile?.mkdirs()
        val url =
            URI(
                "https://github.com/2dust/AndroidLibXrayLite/releases/download/$libv2rayVersionProperty/libv2ray.aar",
            ).toURL()
        logger.lifecycle("Downloading libv2ray.aar ({}) …", libv2rayVersionProperty)
        url.openStream().use { input: InputStream ->
            libv2rayAar.outputStream().use { output: OutputStream ->
                input.copyTo(output)
            }
        }
        logger.lifecycle("Saved to {}", libv2rayAar.absolutePath)
    }
}

tasks.named("preBuild").configure {
    dependsOn(downloadLibv2ray)
}

val hevLibFileName = "libhev-socks5-tunnel.so"
val hevRequiredAbi = "arm64-v8a"
val hevOptionalAbi = "armeabi-v7a"
val hevGeneratedDir = file("$buildDir/generated/hev-jniLibs")

fun existingHevLibCandidates(abi: String): List<java.io.File> {
    val localJni = layout.projectDirectory.file("src/main/jniLibs/$abi/$hevLibFileName").asFile
    val localLegacy = layout.projectDirectory.file("libs/$abi/$hevLibFileName").asFile
    val generated = File(hevGeneratedDir, "$abi/$hevLibFileName")
    return listOf(localJni, localLegacy, generated)
}

fun hasHevLib(abi: String): Boolean {
    return existingHevLibCandidates(abi).any { it.exists() && it.length() > 0L }
}

fun downloadAndExtractHevLib(url: String, abi: String, targetFile: java.io.File) {
    val tmpFile = targetFile.parentFile.resolve("$abi.apk.tmp")
    tmpFile.parentFile?.mkdirs()
    URI(url).toURL().openStream().use { input ->
        tmpFile.outputStream().use { output -> input.copyTo(output) }
    }
    var extracted = false
    ZipInputStream(tmpFile.inputStream().buffered()).use { zip ->
        var entry = zip.nextEntry
        while (entry != null) {
            if (!entry.isDirectory && entry.name == "lib/$abi/$hevLibFileName") {
                targetFile.parentFile.mkdirs()
                targetFile.outputStream().use { out -> zip.copyTo(out) }
                extracted = true
                break
            }
            entry = zip.nextEntry
        }
    }
    tmpFile.delete()
    if (!extracted || !targetFile.exists() || targetFile.length() == 0L) {
        throw GradleException("Failed to extract $hevLibFileName for ABI $abi from $url")
    }
}

val syncHevNativeLibs = tasks.register("syncHevNativeLibs") {
    group = "setup"
    description = "Ensures hev native libs exist for arm64-v8a (and optional armeabi-v7a)."
    doLast {
        val outDir = hevGeneratedDir
        val requiredTarget = outDir.resolve("$hevRequiredAbi/$hevLibFileName")
        val optionalTarget = outDir.resolve("$hevOptionalAbi/$hevLibFileName")

        if (!hasHevLib(hevRequiredAbi)) {
            logger.lifecycle("HEV native lib for $hevRequiredAbi not found locally. Downloading from upstream APK...")
            downloadAndExtractHevLib(
                "https://github.com/2dust/v2rayNG/releases/download/2.0.15/v2rayNG_2.0.15_arm64-v8a.apk",
                hevRequiredAbi,
                requiredTarget,
            )
        }
        if (!hasHevLib(hevRequiredAbi)) {
            throw GradleException("Missing required $hevLibFileName for ABI $hevRequiredAbi")
        }

        if (!hasHevLib(hevOptionalAbi)) {
            try {
                logger.lifecycle("HEV native lib for $hevOptionalAbi not found locally. Downloading optional ABI...")
                downloadAndExtractHevLib(
                    "https://github.com/2dust/v2rayNG/releases/download/2.0.15/v2rayNG_2.0.15_armeabi-v7a.apk",
                    hevOptionalAbi,
                    optionalTarget,
                )
            } catch (e: Exception) {
                logger.warn("Optional ABI $hevOptionalAbi HEV lib download failed: ${e.message}")
            }
        }
    }
}

tasks.named("preBuild").configure {
    dependsOn(syncHevNativeLibs)
}

tasks.withType<KotlinCompile>().configureEach {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}
