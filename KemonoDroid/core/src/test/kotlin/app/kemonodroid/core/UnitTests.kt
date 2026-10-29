package app.kemonodroid.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class UnitTests {
    @Test fun parsesCreatorUrl() {
        val t = UrlParser.parse("https://kemono.su/patreon/user/12345")!!
        assertEquals(Target("kemono.su", "patreon", "12345", null), t)
    }

    @Test fun parsesPostUrlAndApiUrl() {
        assertEquals("999", UrlParser.parse("https://coomer.st/onlyfans/user/abc/post/999?x=1")!!.postId)
        assertEquals("abc", UrlParser.parse("https://kemono.cr/api/v1/fanbox/user/abc")!!.userId)
    }

    @Test fun overrideAndUnknownHost() {
        assertEquals("kemono.cr", UrlParser.parse("https://example.org/patreon/user/1")!!.domain)
        assertEquals("kemono.su", UrlParser.parse("https://kemono.cr/patreon/user/1", "kemono.su")!!.domain)
        assertNull(UrlParser.parse("not a link"))
    }

    @Test fun modes() {
        assertTrue(Filters.matchesMode(FileMode.IMAGES, "a.JPG"))
        assertFalse(Filters.matchesMode(FileMode.IMAGES, "a.mp4"))
        assertTrue(Filters.matchesMode(FileMode.VIDEOS, "a.mp4"))
        assertTrue(Filters.matchesMode(FileMode.ALL, "noext"))
    }

    @Test fun skipRulesWithSize() {
        val r = Filters.parseSkip("WIP, sketch, [200]")
        assertEquals(listOf("wip", "sketch"), r.words)
        assertEquals(200L * 1024 * 1024, r.minBytes)
        assertTrue(Filters.skipPost(r, FilterScope.POSTS, "My wip"))
        assertFalse(Filters.skipPost(r, FilterScope.FILES, "My wip"))
        assertTrue(Filters.skipFile(r, FilterScope.BOTH, "Sketch1.png"))
    }

    @Test fun includeWords() {
        assertTrue(Filters.included(emptyList(), "x"))
        assertTrue(Filters.included(listOf("tifa"), "Post", "TIFA_01.png"))
        assertFalse(Filters.included(listOf("tifa"), "Post", "aerith.png"))
    }

    @Test fun sanitizeAndRemove() {
        assertEquals("a_b_c", Naming.sanitize("a/b:c"))
        assertEquals("_", Naming.sanitize("..."))
        assertTrue(Naming.sanitize("x".repeat(300) + ".png", 50).endsWith(".png"))
        assertEquals("photo.png", Naming.removeWords("photo [HD].png", listOf("[hd]")))
    }

    @Test fun extractsLinks() {
        val html = """<a href="https://mega.nz/file/abc">m</a> and https://drive.google.com/x. """
        assertEquals(listOf("https://mega.nz/file/abc", "https://drive.google.com/x"), Filters.extractLinks(html))
    }

    @Test fun postFilesDeduped() {
        val p = Post("1", file = FileRef("a.png", "/x/a.png"), attachments = listOf(FileRef("a.png", "/x/a.png"), FileRef("b.zip", "/y/b.zip"), FileRef(null, null)))
        assertEquals(listOf("/x/a.png", "/y/b.zip"), p.allFiles().map { it.path })
    }
}
