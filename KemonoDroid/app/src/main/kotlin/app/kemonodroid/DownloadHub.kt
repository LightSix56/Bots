package app.kemonodroid

import app.kemonodroid.core.Progress
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update

data class UiState(
    val running: Boolean = false,
    val paused: Boolean = false,
    val progress: Progress = Progress(),
    val log: List<String> = emptyList(),
)

/** State shared between [DownloadService] (writer) and the UI (reader). */
object DownloadHub {
    private const val MAX_LOG = 300

    val state = MutableStateFlow(UiState())

    fun log(line: String) = state.update { it.copy(log = (it.log + line).takeLast(MAX_LOG)) }

    fun started() = state.update { UiState(running = true, log = it.log) }

    fun progress(p: Progress) = state.update { it.copy(progress = p) }

    fun paused(value: Boolean) = state.update { it.copy(paused = value) }

    fun finished() = state.update { it.copy(running = false, paused = false) }
}
