package app.kemonodroid

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.kemonodroid.core.DownloadEngine
import app.kemonodroid.core.DownloadOptions
import app.kemonodroid.core.KemonoJson
import app.kemonodroid.core.Progress
import app.kemonodroid.core.UrlParser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null
    private var engine: DownloadEngine? = null
    private var lastNotify = 0L

    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> start(intent.getStringExtra(EXTRA_URL).orEmpty(), intent.getStringExtra(EXTRA_OPTIONS).orEmpty())
            ACTION_PAUSE -> setPaused(true)
            ACTION_RESUME -> setPaused(false)
            ACTION_STOP -> job?.cancel()
        }
        return START_NOT_STICKY
    }

    private fun setPaused(value: Boolean) {
        engine?.setPaused(value)
        DownloadHub.paused(value)
        notify(DownloadHub.state.value.progress, force = true)
    }

    private fun start(url: String, optionsJson: String) {
        if (job?.isActive == true) return
        val options = runCatching { KemonoJson.decodeFromString<DownloadOptions>(optionsJson) }.getOrDefault(DownloadOptions())
        val target = UrlParser.parse(url, options.domainOverride)

        createChannel()
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, buildNotification(Progress(), paused = false),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
        )
        DownloadHub.started()
        if (target == null) {
            DownloadHub.log("Not a kemono/coomer creator or post link: $url")
            finish()
            return
        }

        val engine = DownloadEngine(
            client = client,
            storage = MediaStoreStorage(contentResolver),
            log = DownloadHub::log,
            onProgress = { p ->
                DownloadHub.progress(p)
                notify(p, force = p.finished)
            },
        ).also { this.engine = it }

        job = scope.launch {
            try {
                engine.run(target, options)
                DownloadHub.log("Done. Files are in Downloads/KemonoDroid.")
            } catch (e: CancellationException) {
                // stopped by the user; the engine already logged it
            } catch (e: Exception) {
                DownloadHub.log("Failed: ${e.message}")
            } finally {
                finish()
            }
        }
    }

    private fun finish() {
        DownloadHub.finished()
        job = null
        engine = null
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun notify(p: Progress, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - lastNotify < 1000) return
        lastNotify = now
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(p, DownloadHub.state.value.paused))
    }

    private fun buildNotification(p: Progress, paused: Boolean): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val handled = p.done + p.skipped + p.failed
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(if (paused) "Paused" else "Downloading")
            .setContentText("$handled / ${p.files} files, ${p.posts} posts")
            .setProgress(p.files, handled.coerceAtMost(p.files), p.files == 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, if (paused) "Resume" else "Pause", actionIntent(if (paused) ACTION_RESUME else ACTION_PAUSE))
            .addAction(0, "Stop", actionIntent(ACTION_STOP))
            .build()
    }

    private fun actionIntent(action: String) = PendingIntent.getService(
        this, action.hashCode(), Intent(this, DownloadService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE,
    )

    private fun createChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Downloads", NotificationManager.IMPORTANCE_LOW),
        )
    }

    companion object {
        private const val CHANNEL_ID = "downloads"
        private const val NOTIFICATION_ID = 1
        const val ACTION_START = "app.kemonodroid.START"
        const val ACTION_PAUSE = "app.kemonodroid.PAUSE"
        const val ACTION_RESUME = "app.kemonodroid.RESUME"
        const val ACTION_STOP = "app.kemonodroid.STOP"
        private const val EXTRA_URL = "url"
        private const val EXTRA_OPTIONS = "options"

        fun start(context: Context, url: String, options: DownloadOptions) {
            val intent = Intent(context, DownloadService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_OPTIONS, KemonoJson.encodeToString(DownloadOptions.serializer(), options))
            ContextCompat.startForegroundService(context, intent)
        }

        fun send(context: Context, action: String) {
            context.startService(Intent(context, DownloadService::class.java).setAction(action))
        }
    }
}
