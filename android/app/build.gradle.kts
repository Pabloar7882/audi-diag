plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val generatedSensorCatalogAssets = layout.buildDirectory.dir("generated/sensorCatalogAssets")
val copySensorCatalogAsset = tasks.register("copySensorCatalogAsset", org.gradle.api.tasks.Copy::class) {
    from(rootProject.file("../data/sensor_catalog.v1.json"))
    into(generatedSensorCatalogAssets)
}

android {
    namespace = "pt.fisafn"
    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    defaultConfig {
        applicationId = "pt.fisafn"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.4"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    sourceSets {
        getByName("main").assets.srcDir(generatedSensorCatalogAssets)
        getByName("test").resources.srcDir(generatedSensorCatalogAssets)
    }
}

tasks.named("preBuild").configure {
    dependsOn(copySensorCatalogAsset)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.github.mik3y:usb-serial-for-android:3.11.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20180813")
}
