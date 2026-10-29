package app.kemonodroid.core

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.Job
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.job
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.OkHttpClient
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

class DownloadEngine(
    private val client: OkHttpClient,
    private val storage: Storage,
    private val scheme: String = "https",
    private val retryDelayMs: Long = 3000,
    private val log: (String) -> Unit = {},
    private val onProgress: (Progress) -> Unit = {},
) {
    private val paused = MutableStateFlow(false)

    fun setPaused(value: Boolean) {
        paused.value = value
    }

    private val posts = AtomicInteger()
    private val files = AtomicInteger()
    private val done = AtomicInteger()
    private val skipped = AtomicInteger()
    private val failed = AtomicInteger()
    private val bytes = AtomicLong()
    private val active = ConcurrentHashMap<String, Int>()

    private fun report(finished: Boolean = false) = onProgress(
        Progress(
            posts.get(), files.get(), done.get(), skipped.get(), failed.get(), bytes.get(),
            active.entries.sortedBy { it.key }.map { "${it.key} ${it.value}%" }, finished,
        ),
    )

    /** Downloads everything [target] points at. Cancel the calling coroutine to stop. */
    suspend fun run(target: Target, options: DownloadOptions) {
        val api = KemonoApi(client, options.cookie, scheme, retryDelayMs)
        val include = Filters.words(options.includeWords)
        val skip = Filters.parseSkip(options.skipWords)
        val remove = Filters.words(options.removeWords)
        val creator = Naming.sanitize(api.creatorName(target) ?: "${target.service}_${target.userId}")
        val root = listOf(creator)
        val links = LinkedHashSet<String>()
        log("Creator: $creator (${target.service}, ${target.domain})")

        try {
            if (target.postId != null) {
                handlePost(api, target, api.post(target, target.postId), root, options, include, skip, remove, links)
            } else {
                var page = options.startPage.coerceAtLeast(1)
                while (true) {
                    if (options.endPage != null && page > options.endPage) break
                    awaitResume()
                    val batch = api.posts(target, (page - 1) * KemonoApi.PAGE_SIZE)
                    if (batch.isEmpty()) {
                        log("Reached the end of the post list.")
                        break
                    }
                    log("Page $page: ${batch.size} posts")
                    for (p in batch) handlePost(api, target, p, root, options, include, skip, remove, links)
                    if (batch.size < KemonoApi.PAGE_SIZE) break
                    page++
                }
            }
            if (options.mode == FileMode.LINKS && links.isNotEmpty()) {
                storage.writeText(root, "links.txt", links.joinToString("\n"))
                log("Saved ${links.size} links to links.txt")
            }
        } catch (e: CancellationException) {
            log("Stopped.")
            throw e
        } catch (e: Exception) {
            log("Error: ${e.message}")
            throw e
        } finally {
            active.clear()
            report(finished = true)
        }
    }

    private suspend fun awaitResume() {
        if (paused.value) {
            log("Paused.")
            paused.first { !it }
            log("Resumed.")
        }
        currentCoroutineContext().ensureActive()
    }

    private suspend fun handlePost(
        api: KemonoApi, t: Target, post: Post, root: List<String>, o: DownloadOptions,
        include: List<String>, skip: Filters.SkipRules, remove: List<String>, links: MutableSet<String>,
    ) {
        posts.incrementAndGet()
        if (Filters.skipPost(skip, o.skipScope, post.title)) {
            log("Skip post (word): ${post.title}")
            report()
            return
        }
        if (o.mode == FileMode.LINKS) {
            links += Filters.extractLinks(post.content)
            report()
            return
        }

        val dir = if (o.folderPerPost) root + Naming.postFolder(post) else root
        val used = HashSet<String>()
        val wanted = post.allFiles().mapIndexedNotNull { i, ref ->
            var name = Naming.fileName(ref, remove)
            if (!Filters.matchesMode(o.mode, name)) return@mapIndexedNotNull null
            if (!Filters.included(include, post.title, name)) return@mapIndexedNotNull null
            if (Filters.skipFile(skip, o.skipScope, name)) {
                skipped.incrementAndGet()
                return@mapIndexedNotNull null
            }
            // same name from different paths: keep both
            if (!used.add(name.lowercase())) name = "${i + 1}_$name"
            used += name.lowercase()
            ref to name
        }
        files.addAndGet(wanted.size)
        report()
        if (wanted.isEmpty()) return

        val referer = api.postPage(t, post.id)
        val gate = Semaphore(o.threads.coerceIn(1, 8))
        coroutineScope {
            wanted.map { (ref, name) ->
                async(Dispatchers.IO) {
                    gate.withPermit { downloadFile(api, t, ref, dir, name, referer, skip.minBytes) }
                }
            }.awaitAll()
        }
    }

    private suspend fun downloadFile(
        api: KemonoApi, t: Target, ref: FileRef, dir: List<String>, name: String, referer: String, minBytes: Long,
    ) {
        awaitResume()
        if (storage.exists(dir, name)) {
            skipped.incrementAndGet()
            report()
            return
        }
        val url = api.fileUrl(t.domain, ref.path!!)
        var lastError: Exception? = null
        for (attempt in 0 until 3) {
            awaitResume()
            try {
                when (transfer(api, url, referer, dir, name, minBytes)) {
                    Outcome.DONE -> done.incrementAndGet()
                    Outcome.TOO_SMALL -> {
                        skipped.incrementAndGet()
                        log("Skip (smaller than limit): $name")
                    }
                }
                report()
                return
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                lastError = e
                active.remove(name)
                delay(retryDelayMs * (attempt + 1))
            }
        }
        failed.incrementAndGet()
        log("Failed: $name (${lastError?.message})")
        report()
    }

    private enum class Outcome { DONE, TOO_SMALL }

    private suspend fun transfer(
        api: KemonoApi, url: String, referer: String, dir: List<String>, name: String, minBytes: Long,
    ): Outcome {
        val call = client.newCall(api.requestBuilder(url, referer).header("Accept", "*/*").build())
        val job = currentCoroutineContext().job
        return coroutineScope {
            // aborts the blocking read as soon as this coroutine is cancelled
            val watcher = launch { try { awaitCancellation() } finally { call.cancel() } }
            try {
                execute(call, dir, name, minBytes, job)
            } finally {
                watcher.cancel()
            }
        }
    }

    private fun execute(call: Call, dir: List<String>, name: String, minBytes: Long, job: Job): Outcome {
        return call.execute().use { resp ->
            if (!resp.isSuccessful) throw ApiException(resp.code, "HTTP ${resp.code}")
            val body = resp.body ?: throw IOException("Empty body")
            val total = body.contentLength()
            if (minBytes > 0 && total in 0 until minBytes) return@use Outcome.TOO_SMALL

            val sink = storage.create(dir, name)
            try {
                val buf = ByteArray(64 * 1024)
                var read = 0L
                body.byteStream().use { input ->
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        if (paused.value) awaitResumeBlocking(job)
                        sink.out.write(buf, 0, n)
                        read += n
                        bytes.addAndGet(n.toLong())
                        if (total > 0) active[name] = (read * 100 / total).toInt()
                    }
                }
                if (total >= 0 && read != total) throw IOException("Incomplete: $read of $total bytes")
                sink.commit()
            } catch (e: Throwable) {
                sink.abort()
                throw e
            } finally {
                active.remove(name)
            }
            Outcome.DONE
        }
    }

    /** Pause inside the read loop: blocks this IO thread until resumed (or cancelled). */
    private fun awaitResumeBlocking(job: Job) {
        while (paused.value && job.isActive) Thread.sleep(200)
        if (!job.isActive) throw CancellationException("Stopped while paused")
    }
}
