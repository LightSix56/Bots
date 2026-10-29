package app.kemonodroid.core

import java.io.File
import java.io.OutputStream

/** A file being written; [commit] publishes it, [abort] discards the partial data. */
interface Sink {
    val out: OutputStream
    fun commit()
    fun abort()
}

/** Where downloads go. [dir] is a path of folder names below the storage root. */
interface Storage {
    fun exists(dir: List<String>, name: String): Boolean
    fun create(dir: List<String>, name: String): Sink

    fun writeText(dir: List<String>, name: String, text: String) {
        val sink = create(dir, name)
        try {
            sink.out.write(text.toByteArray())
            sink.commit()
        } catch (e: Exception) {
            sink.abort()
            throw e
        }
    }
}

class FileStorage(private val root: File) : Storage {
    private fun dirOf(dir: List<String>) = dir.fold(root) { f, part -> File(f, part) }

    override fun exists(dir: List<String>, name: String) = File(dirOf(dir), name).exists()

    override fun create(dir: List<String>, name: String): Sink {
        val folder = dirOf(dir).also { it.mkdirs() }
        val tmp = File(folder, "$name.part")
        val target = File(folder, name)
        val stream = tmp.outputStream().buffered()
        return object : Sink {
            override val out: OutputStream = stream
            override fun commit() {
                stream.close()
                target.delete()
                tmp.renameTo(target)
            }

            override fun abort() {
                runCatching { stream.close() }
                tmp.delete()
            }
        }
    }
}
