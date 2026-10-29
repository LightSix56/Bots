package app.kemonodroid.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

val KemonoJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    isLenient = true
}

@Serializable
data class FileRef(val name: String? = null, val path: String? = null)

@Serializable
data class Post(
    val id: String,
    val user: String = "",
    val service: String = "",
    val title: String = "",
    val content: String? = null,
    val published: String? = null,
    val added: String? = null,
    val file: FileRef? = null,
    val attachments: List<FileRef> = emptyList(),
) {
    val date: String get() = (published ?: added).orEmpty().take(10)

    /** Main file first, then attachments, without duplicate paths. */
    fun allFiles(): List<FileRef> =
        (listOfNotNull(file) + attachments).filter { !it.path.isNullOrBlank() }.distinctBy { it.path }
}

/** Where a pasted URL points: a whole creator, or a single post when [postId] is set. */
data class Target(val domain: String, val service: String, val userId: String, val postId: String?)

enum class FileMode { ALL, IMAGES, VIDEOS, ARCHIVES, AUDIO, LINKS }

enum class FilterScope { POSTS, FILES, BOTH }

@Serializable
data class DownloadOptions(
    val mode: FileMode = FileMode.ALL,
    /** Comma-separated; a post/file is kept only if it matches one of them. */
    val includeWords: String = "",
    /** Comma-separated; `[200]` additionally skips files smaller than 200 MB. */
    val skipWords: String = "",
    val skipScope: FilterScope = FilterScope.BOTH,
    val removeWords: String = "",
    val folderPerPost: Boolean = true,
    val startPage: Int = 1,
    val endPage: Int? = null,
    val threads: Int = 3,
    val cookie: String = "",
    /** Overrides the host from the pasted URL (e.g. to switch mirrors). */
    val domainOverride: String = "",
)

data class Progress(
    val posts: Int = 0,
    val files: Int = 0,
    val done: Int = 0,
    val skipped: Int = 0,
    val failed: Int = 0,
    val bytes: Long = 0,
    val active: List<String> = emptyList(),
    val finished: Boolean = false,
)
