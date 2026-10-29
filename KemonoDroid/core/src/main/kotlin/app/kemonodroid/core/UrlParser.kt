package app.kemonodroid.core

object UrlParser {
    private val pattern = Regex(
        """^(?:https?://)?([^/\s]+)/(?:api/v1/)?([a-z0-9_]+)/user/([^/?#\s]+)(?:/post/([^/?#\s]+))?""",
        RegexOption.IGNORE_CASE,
    )

    private val knownHosts = listOf(
        "kemono.su", "kemono.party", "kemono.cr", "coomer.su", "coomer.party", "coomer.st",
        "pawchive.st", "pawchive.pw",
    )

    fun parse(input: String, domainOverride: String = ""): Target? {
        val m = pattern.find(input.trim()) ?: return null
        val host = m.groupValues[1].lowercase()
        val domain = domainOverride.trim().ifEmpty {
            if (knownHosts.any { host == it || host.endsWith(".$it") } || host.contains(':')) host else "kemono.cr"
        }
        return Target(domain, m.groupValues[2].lowercase(), m.groupValues[3], m.groupValues[4].ifEmpty { null })
    }
}
