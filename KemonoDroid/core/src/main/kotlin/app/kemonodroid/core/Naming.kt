package app.kemonodroid.core

object Naming {
    private val illegal = Regex("""[\\/:*?"<>|\u0000-\u001f]""")

    fun sanitize(name: String, maxLen: Int = 100): String {
        val cleaned = illegal.replace(name, "_").replace(Regex("\\s+"), " ").trim().trim('.')
        if (cleaned.isEmpty()) return "_"
        if (cleaned.length <= maxLen) return cleaned
        // keep the extension when shortening a file name
        val dot = cleaned.lastIndexOf('.')
        val ext = if (dot > 0 && cleaned.length - dot <= 8) cleaned.substring(dot) else ""
        return cleaned.substring(0, maxLen - ext.length).trimEnd() + ext
    }

    fun removeWords(name: String, words: List<String>): String {
        if (words.isEmpty()) return name
        val ext = name.substringAfterLast('.', "").let { if (it.isEmpty()) "" else ".$it" }
        var base = name.removeSuffix(ext)
        for (w in words) base = base.replace(w, "", ignoreCase = true)
        return base.replace(Regex("\\s+"), " ").trim().ifEmpty { "file" } + ext
    }

    fun postFolder(post: Post): String =
        sanitize("${post.date} ${post.title}".trim(), 80) + " [${post.id}]"

    fun fileName(ref: FileRef, removeWords: List<String>): String {
        val raw = ref.name?.takeIf { it.isNotBlank() } ?: ref.path.orEmpty().substringAfterLast('/')
        return sanitize(removeWords(raw, removeWords), 150)
    }
}
