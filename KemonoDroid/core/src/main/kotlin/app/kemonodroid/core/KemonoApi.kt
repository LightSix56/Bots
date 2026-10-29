package app.kemonodroid.core

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

class ApiException(val code: Int, message: String) : IOException(message)

const val BROWSER_UA =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class KemonoApi(
    private val client: OkHttpClient,
    private val cookie: String = "",
    private val scheme: String = "https",
    private val retryDelayMs: Long = 3000,
) {
    companion object {
        const val PAGE_SIZE = 50
        private const val FIELDS = "id,user,service,title,shared_file,added,published,edited,file,attachments,tags,content"
        private const val MAX_ATTEMPTS = 4
    }

    fun requestBuilder(url: String, referer: String? = null): Request.Builder =
        Request.Builder().url(url)
            .header("User-Agent", BROWSER_UA)
            // kemono's anti-bot layer rejects the default `*/*` on API routes
            .header("Accept", "text/css")
            .apply {
                referer?.let { header("Referer", it) }
                if (cookie.isNotBlank()) header("Cookie", cookie)
            }

    private suspend fun getJson(url: String): JsonElement = withContext(Dispatchers.IO) {
        var last: IOException? = null
        for (attempt in 0 until MAX_ATTEMPTS) {
            try {
                client.newCall(requestBuilder(url).build()).execute().use { resp ->
                    when {
                        resp.isSuccessful -> return@withContext KemonoJson.parseToJsonElement(resp.body!!.string())
                        resp.code == 429 || resp.code >= 500 -> {
                            val wait = resp.header("Retry-After")?.toLongOrNull()?.times(1000)
                            last = ApiException(resp.code, "HTTP ${resp.code} for $url")
                            delay(wait ?: (retryDelayMs * (attempt + 1)))
                        }
                        else -> throw ApiException(resp.code, "HTTP ${resp.code} for $url")
                    }
                }
            } catch (e: ApiException) {
                throw e
            } catch (e: IOException) {
                last = e
                delay(retryDelayMs * (attempt + 1))
            }
        }
        throw last ?: IOException("Failed: $url")
    }

    private fun base(domain: String) = "$scheme://$domain/api/v1"

    /** One page (up to 50 posts). A 400 answer means "past the last page". */
    suspend fun posts(t: Target, offset: Int): List<Post> {
        val url = "${base(t.domain)}/${t.service}/user/${t.userId}/posts?o=$offset&fields=$FIELDS"
        val json = try {
            getJson(url)
        } catch (e: ApiException) {
            if (e.code == 400) return emptyList()
            throw e
        }
        val array = when (json) {
            is JsonArray -> json
            is JsonObject -> (json["posts"] ?: json["results"]) as? JsonArray ?: JsonArray(emptyList())
            else -> JsonArray(emptyList())
        }
        return array.map { KemonoJson.decodeFromJsonElement<Post>(it) }
    }

    suspend fun post(t: Target, postId: String): Post {
        val json = getJson("${base(t.domain)}/${t.service}/user/${t.userId}/post/$postId")
        val node = when (json) {
            is JsonArray -> json.first()
            else -> json
        }.let { if (it is JsonObject && it["post"] is JsonObject) it["post"]!! else it }
        return KemonoJson.decodeFromJsonElement<Post>(node)
    }

    /** Creator display name, or null if the profile can't be read. */
    suspend fun creatorName(t: Target): String? = try {
        getJson("${base(t.domain)}/${t.service}/user/${t.userId}/profile")
            .jsonObject["name"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }
    } catch (e: Exception) {
        null
    }

    fun fileUrl(domain: String, path: String): String =
        if (path.startsWith("http")) path else "$scheme://$domain/data${if (path.startsWith("/")) path else "/$path"}"

    fun postPage(t: Target, postId: String) = "$scheme://${t.domain}/${t.service}/user/${t.userId}/post/$postId"
}
