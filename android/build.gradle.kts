plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.20" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.20" apply false
    id("com.google.devtools.ksp") version "2.0.20-1.0.25" apply false
}

/*
 * OneDrive's sync client holds handles on files inside build/ while Gradle is
 * rewriting them, which surfaces as AccessDeniedException on tasks like
 * mergeDebugResources. It looks like a random permissions bug; it is not.
 *
 * The real fix is keeping the checkout outside OneDrive, which this one is.
 * This guard only fires if someone clones into a synced folder anyway, and
 * then sends build output somewhere the sync client does not watch.
 * Override with -PbuildRoot=<path>.
 */
val explicitBuildRoot = findProperty("buildRoot") as String?
val inSyncedFolder = rootDir.absolutePath.contains("OneDrive", ignoreCase = true)

val relocatedRoot: String? = explicitBuildRoot
    ?: if (inSyncedFolder) System.getenv("LOCALAPPDATA")?.let { "$it\\SentinelBuild" } else null

if (relocatedRoot != null) {
    logger.lifecycle("Build output relocated to $relocatedRoot (checkout is in a synced folder)")
    allprojects {
        val slug = path.replace(":", "_").trim('_').ifEmpty { "root" }
        layout.buildDirectory.set(File(relocatedRoot, slug))
    }
}
