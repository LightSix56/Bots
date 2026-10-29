package app.kemonodroid.core

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.io.File
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class EngineTest {
    private val png = ByteArray(2048) { it.toByte() }
    private val zip = ByteArray(4096) { 1 }

    private fun server(requests: MutableList<RecordedRequest> = mutableListOf()) = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path.endsWith("/profile") -> MockResponse().setBody("""{"name":"Cool/Artist"}""")
                    path.contains("/posts?o=0") -> MockResponse().setBody(
                        """[{"id":"1","title":"First: post","published":"2024-05-06T10:00:00",
                           "file":{"name":"a.png","path":"/aa/a.png"},
                           "attachments":[{"name":"b.zip","path":"/bb/b.zip"}],
                           "content":"see https://mega.nz/x"},
                          {"id":"2","title":"WIP stuff","published":"2024-05-07","file":{},"attachments":[]}]""",
                    )
                    path.contains("/posts?o=") -> MockResponse().setResponseCode(400)
                    path.contains("/post/1") -> MockResponse().setBody(
                        """{"post":{"id":"1","title":"First","file":{"name":"a.png","path":"/aa/a.png"}}}""",
                    )
                    path == "/data/aa/a.png" -> MockResponse().setBody(okio.Buffer().write(png))
                    path == "/data/bb/b.zip" -> MockResponse().setBody(okio.Buffer().write(zip))
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        start()
    }

    private fun engine(root: File, srv: MockWebServer, logs: MutableList<String> = mutableListOf()) =
        DownloadEngine(OkHttpClient(), FileStorage(root), scheme = "http", retryDelayMs = 10, log = { logs += it })

    private fun target(srv: MockWebServer, post: String? = null) =
        Target("${srv.hostName}:${srv.port}", "patreon", "42", post)

    @Test fun downloadsCreatorWithFolders() = runBlocking {
        val srv = server()
        val root = Files.createTempDirectory("kd").toFile()
        engine(root, srv).run(target(srv), DownloadOptions(skipWords = "wip"))
        val postDir = File(root, "Cool_Artist").listFiles()!!.single()
        assertEquals("2024-05-06 First_ post [1]", postDir.name)
        assertEquals(png.size.toLong(), File(postDir, "a.png").length())
        assertEquals(zip.size.toLong(), File(postDir, "b.zip").length())
        assertFalse(postDir.listFiles()!!.any { it.name.endsWith(".part") })
        srv.shutdown()
    }

    @Test fun modeFilterAndNoPostFolder() = runBlocking {
        val srv = server()
        val root = Files.createTempDirectory("kd").toFile()
        engine(root, srv).run(target(srv), DownloadOptions(mode = FileMode.IMAGES, folderPerPost = false))
        assertEquals(listOf("a.png"), File(root, "Cool_Artist").list()!!.toList())
        srv.shutdown()
    }

    @Test fun singlePostAndSkipExisting() = runBlocking {
        val srv = server()
        val root = Files.createTempDirectory("kd").toFile()
        val opts = DownloadOptions(folderPerPost = false)
        engine(root, srv).run(target(srv, "1"), opts)
        val progress = mutableListOf<Progress>()
        DownloadEngine(OkHttpClient(), FileStorage(root), "http", 10, onProgress = { progress += it })
            .run(target(srv, "1"), opts)
        assertEquals(1, progress.last().skipped)
        assertEquals(0, progress.last().done)
        assertTrue(progress.last().finished)
        srv.shutdown()
    }

    @Test fun linksMode() = runBlocking {
        val srv = server()
        val root = Files.createTempDirectory("kd").toFile()
        engine(root, srv).run(target(srv), DownloadOptions(mode = FileMode.LINKS))
        assertEquals("https://mega.nz/x", File(root, "Cool_Artist/links.txt").readText())
        srv.shutdown()
    }

    @Test fun sendsRefererAndCookie() = runBlocking {
        val reqs = mutableListOf<RecordedRequest>()
        val srv = server(reqs)
        val root = Files.createTempDirectory("kd").toFile()
        engine(root, srv).run(target(srv, "1"), DownloadOptions(cookie = "session=abc"))
        val file = reqs.first { it.path == "/data/aa/a.png" }
        assertEquals("session=abc", file.getHeader("Cookie"))
        assertTrue(file.getHeader("Referer")!!.endsWith("/patreon/user/42/post/1"))
        srv.shutdown()
    }
}
