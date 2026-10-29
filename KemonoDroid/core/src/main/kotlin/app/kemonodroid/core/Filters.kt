package app.kemonodroid.core

object Filters {
    private val images = setOf("jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "avif")
    private val videos = setOf("mp4", "mov", "mkv", "webm", "avi", "wmv", "flv", "m4v", "mpg", "mpeg", "ts")
    private val archives = setOf("zip", "rar", "7z", "tar", "gz", "bz2", "xz")
    private val audio = setOf("mp3", "wav", "flac", "aac", "ogg", "m4a", "opus", "wma")

    fun extension(name: String): String = name.substringAfterLast('.', "").lowercase()

    fun matchesMode(mode: FileMode, name: String): Boolean {
        val ext = extension(name)
        return when (mode) {
            FileMode.ALL -> true
            FileMode.IMAGES -> ext in images
            FileMode.VIDEOS -> ext in videos
            FileMode.ARCHIVES -> ext in archives
            FileMode.AUDIO -> ext in audio
            FileMode.LINKS -> false
        }
    }

    fun words(raw: String): List<String> =
        raw.split(',').map { it.trim().lowercase() }.filter { it.isNotEmpty() }

    private val sizeToken = Regex("""\[\s*(\d+(?:\.\d+)?)\s*]""")

    /** Parsed `skipWords`: plain words plus an optional `[N]` minimum size in MB. */
    class SkipRules(val words: List<String>, val minBytes: Long) {
        fun hits(text: String): Boolean {
            val t = text.lowercase()
            return words.any { it in t }
        }
    }

    fun parseSkip(raw: String): SkipRules {
        val mb = sizeToken.find(raw)?.groupValues?.get(1)?.toDoubleOrNull() ?: 0.0
        return SkipRules(words(sizeToken.replace(raw, "")), (mb * 1024 * 1024).toLong())
    }

    fun skipPost(rules: SkipRules, scope: FilterScope, title: String) =
        scope != FilterScope.FILES && rules.hits(title)

    fun skipFile(rules: SkipRules, scope: FilterScope, name: String) =
        scope != FilterScope.POSTS && rules.hits(name)

    /** Empty include list keeps everything. */
    fun included(include: List<String>, vararg texts: String) =
        include.isEmpty() || texts.any { t -> include.any { it in t.lowercase() } }

    private val linkRegex = Regex("""https?://[^\s"'<>)]+""")

    fun extractLinks(html: String?): List<String> =
        if (html.isNullOrBlank()) emptyList()
        else linkRegex.findAll(html).map { it.value.trimEnd('.', ',', ';') }.distinct().toList()
}
