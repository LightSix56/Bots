package app.kemonodroid

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.kemonodroid.core.DownloadOptions
import app.kemonodroid.core.FileMode
import app.kemonodroid.core.FilterScope
import app.kemonodroid.core.UrlParser

class MainActivity : ComponentActivity() {
    private val sharedText = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        sharedText.value = extractShared(intent)
        setContent {
            val dark = isSystemInDarkTheme()
            val ctx = LocalContext.current
            val scheme = when {
                Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
                dark -> darkColorScheme()
                else -> lightColorScheme()
            }
            MaterialTheme(colorScheme = scheme) {
                Surface(Modifier.fillMaxSize()) {
                    MainScreen(Prefs(this), sharedText.value)
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        sharedText.value = extractShared(intent)
    }

    private fun extractShared(intent: Intent?): String? =
        if (intent?.action == Intent.ACTION_SEND) intent.getStringExtra(Intent.EXTRA_TEXT) else null
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MainScreen(prefs: Prefs, shared: String?) {
    val context = LocalContext.current
    val ui by DownloadHub.state.collectAsStateWithLifecycle()
    val saved = remember { prefs.options }

    var url by rememberSaveable(shared) { mutableStateOf(shared ?: prefs.url) }
    var mode by rememberSaveable { mutableStateOf(saved.mode) }
    var include by rememberSaveable { mutableStateOf(saved.includeWords) }
    var skip by rememberSaveable { mutableStateOf(saved.skipWords) }
    var skipScope by rememberSaveable { mutableStateOf(saved.skipScope) }
    var remove by rememberSaveable { mutableStateOf(saved.removeWords) }
    var perPost by rememberSaveable { mutableStateOf(saved.folderPerPost) }
    var startPage by rememberSaveable { mutableStateOf(saved.startPage.toString()) }
    var endPage by rememberSaveable { mutableStateOf(saved.endPage?.toString().orEmpty()) }
    var threads by rememberSaveable { mutableStateOf(saved.threads.toString()) }
    var cookie by rememberSaveable { mutableStateOf(saved.cookie) }
    var domain by rememberSaveable { mutableStateOf(saved.domainOverride) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }

    val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    fun buildOptions() = DownloadOptions(
        mode = mode, includeWords = include, skipWords = skip, skipScope = skipScope, removeWords = remove,
        folderPerPost = perPost,
        startPage = startPage.toIntOrNull()?.coerceAtLeast(1) ?: 1,
        endPage = endPage.toIntOrNull(),
        threads = threads.toIntOrNull()?.coerceIn(1, 8) ?: 3,
        cookie = cookie.trim(), domainOverride = domain.trim(),
    )

    fun start() {
        if (UrlParser.parse(url, domain) == null) {
            error = "Paste a link like https://kemono.su/patreon/user/12345"
            return
        }
        error = null
        val options = buildOptions()
        prefs.url = url
        prefs.options = options
        if (Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        DownloadService.start(context, url.trim(), options)
    }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { Spacer(Modifier.padding(top = 12.dp)); Text("KemonoDroid", style = MaterialTheme.typography.headlineSmall) }

        item {
            OutlinedTextField(
                value = url, onValueChange = { url = it },
                label = { Text("Creator or post link") },
                isError = error != null, supportingText = error?.let { { Text(it) } },
                modifier = Modifier.fillMaxWidth(),
            )
            TextButton(onClick = {
                val clip = (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
                clip?.getItemAt(0)?.text?.let { url = it.toString() }
            }) { Text("Paste from clipboard") }
        }

        item {
            Text("What to download", style = MaterialTheme.typography.labelLarge)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FileMode.entries.forEach { m ->
                    FilterChip(selected = mode == m, onClick = { mode = m }, label = { Text(modeLabel(m)) })
                }
            }
        }

        item { Field("Only if name contains (comma separated)", include) { include = it } }
        item { Field("Skip words, [200] = skip files under 200 MB", skip) { skip = it } }
        item {
            Text("Skip words apply to", style = MaterialTheme.typography.labelLarge)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterScope.entries.forEach { s ->
                    FilterChip(selected = skipScope == s, onClick = { skipScope = s }, label = { Text(scopeLabel(s)) })
                }
            }
        }
        item { Field("Remove from file names", remove) { remove = it } }

        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Separate folder per post", Modifier.padding(top = 12.dp))
                Switch(checked = perPost, onCheckedChange = { perPost = it })
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumberField("From page", startPage, Modifier.weight(1f)) { startPage = it }
                NumberField("To page", endPage, Modifier.weight(1f)) { endPage = it }
                NumberField("Threads", threads, Modifier.weight(1f)) { threads = it }
            }
        }

        item { Field("Session cookie (optional, e.g. session=...)", cookie) { cookie = it } }
        item { Field("Use another domain (optional, e.g. kemono.su)", domain) { domain = it } }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!ui.running) {
                    Button(onClick = ::start) { Text("Download") }
                } else {
                    OutlinedButton(onClick = {
                        DownloadService.send(context, if (ui.paused) DownloadService.ACTION_RESUME else DownloadService.ACTION_PAUSE)
                    }) { Text(if (ui.paused) "Resume" else "Pause") }
                    Button(onClick = { DownloadService.send(context, DownloadService.ACTION_STOP) }) { Text("Stop") }
                }
            }
        }

        item {
            val p = ui.progress
            val handled = p.done + p.skipped + p.failed
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (p.files > 0) {
                        LinearProgressIndicator(progress = { handled.toFloat() / p.files }, modifier = Modifier.fillMaxWidth())
                    } else if (ui.running) {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                    }
                    Text("Posts ${p.posts} · Files $handled/${p.files} · Skipped ${p.skipped} · Failed ${p.failed}")
                    Text("Downloaded ${formatBytes(p.bytes)}", style = MaterialTheme.typography.bodySmall)
                    p.active.take(6).forEach { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 1) }
                }
            }
        }

        item { Text("Log", style = MaterialTheme.typography.labelLarge) }
        items(ui.log.asReversed()) {
            Text(it, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
        }
        item { Spacer(Modifier.padding(bottom = 24.dp)) }
    }
}

@Composable
private fun Field(label: String, value: String, onChange: (String) -> Unit) {
    OutlinedTextField(value = value, onValueChange = onChange, label = { Text(label) }, modifier = Modifier.fillMaxWidth())
}

@Composable
private fun NumberField(label: String, value: String, modifier: Modifier, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value, onValueChange = { onChange(it.filter(Char::isDigit)) }, label = { Text(label) },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), singleLine = true, modifier = modifier,
    )
}

private fun modeLabel(m: FileMode) = when (m) {
    FileMode.ALL -> "All"
    FileMode.IMAGES -> "Images"
    FileMode.VIDEOS -> "Videos"
    FileMode.ARCHIVES -> "Archives"
    FileMode.AUDIO -> "Audio"
    FileMode.LINKS -> "Links only"
}

private fun scopeLabel(s: FilterScope) = when (s) {
    FilterScope.POSTS -> "Posts"
    FilterScope.FILES -> "Files"
    FilterScope.BOTH -> "Both"
}

private fun formatBytes(b: Long): String = when {
    b >= 1L shl 30 -> "%.2f GB".format(b / (1L shl 30).toDouble())
    b >= 1L shl 20 -> "%.1f MB".format(b / (1L shl 20).toDouble())
    else -> "%d KB".format(b / 1024)
}
