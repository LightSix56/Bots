package app.kemonodroid

import android.content.Context
import app.kemonodroid.core.DownloadOptions
import app.kemonodroid.core.KemonoJson

class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("kemonodroid", Context.MODE_PRIVATE)

    var url: String
        get() = sp.getString("url", "").orEmpty()
        set(v) = sp.edit().putString("url", v).apply()

    var options: DownloadOptions
        get() = runCatching { KemonoJson.decodeFromString<DownloadOptions>(sp.getString("options", "").orEmpty()) }
            .getOrDefault(DownloadOptions())
        set(v) = sp.edit().putString("options", KemonoJson.encodeToString(DownloadOptions.serializer(), v)).apply()
}
