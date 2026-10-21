pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "MARK Mobile"
include(":app")

include(":wear", ":wear-protocol")
project(":wear").projectDir = file("../mark-wear/app")
project(":wear-protocol").projectDir = file("../mark-wear/protocol")

include(":companion", ":store-test")
project(":companion").projectDir = file("../mark-companion/app")
project(":store-test").projectDir = file("../mark-companion/store-test")
