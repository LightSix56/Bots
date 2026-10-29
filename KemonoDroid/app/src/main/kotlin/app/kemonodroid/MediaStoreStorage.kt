package app.kemonodroid

import android.content.ContentResolver
import android.content.ContentValues
import android.os.Environment
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import app.kemonodroid.core.Sink
import app.kemonodroid.core.Storage
import java.io.OutputStream

/** Saves into the public Downloads folder (Downloads/KemonoDroid/...), no storage permission needed. */
class MediaStoreStorage(
    private val resolver: ContentResolver,
    private val rootFolder: String = "KemonoDroid",
) : Storage {
    private val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI

    private fun relativePath(dir: List<String>) =
        (listOf(Environment.DIRECTORY_DOWNLOADS, rootFolder) + dir).joinToString("/") + "/"

    override fun exists(dir: List<String>, name: String): Boolean =
        resolver.query(
            collection,
            arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME}=? AND ${MediaStore.Downloads.RELATIVE_PATH}=?",
            arrayOf(name, relativePath(dir)),
            null,
        )?.use { it.moveToFirst() } ?: false

    override fun create(dir: List<String>, name: String): Sink {
        val ext = name.substringAfterLast('.', "").lowercase()
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, relativePath(dir))
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values) ?: error("Cannot create $name in Downloads")
        val stream = resolver.openOutputStream(uri)?.buffered() ?: error("Cannot open $name for writing")
        return object : Sink {
            override val out: OutputStream = stream

            override fun commit() {
                stream.close()
                resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            }

            override fun abort() {
                runCatching { stream.close() }
                runCatching { resolver.delete(uri, null, null) }
            }
        }
    }
}
