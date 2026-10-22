"""The Markdown converter behind the site's articles: python -m unittest discover tests"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("minimd", ROOT / "tools" / "minimd.py")
minimd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(minimd)
convert, inline, Unsupported = minimd.convert, minimd.inline, minimd.UnsupportedMarkdown


class Inline(unittest.TestCase):
    def test_emphasis_code_and_links(self):
        self.assertEqual(inline("a **bold** and *italic* word"), "a <strong>bold</strong> and <em>italic</em> word")
        self.assertEqual(inline("see [the repo](https://example.com/x?a=1&b=2)"),
                         'see <a href="https://example.com/x?a=1&amp;b=2">the repo</a>')
        self.assertEqual(inline("use `foo_bar` here"), "use <code>foo_bar</code> here")

    def test_code_is_left_alone(self):
        self.assertEqual(inline("`*not* **bold** [x](y)`"), "<code>*not* **bold** [x](y)</code>")
        self.assertEqual(inline("`a < b && c > d`"), "<code>a &lt; b &amp;&amp; c &gt; d</code>")
        self.assertEqual(inline("`kAudio*Key` and `bytes`"), "<code>kAudio*Key</code> and <code>bytes</code>")

    def test_text_is_escaped(self):
        self.assertEqual(inline("1 < 2 & 3 > 2"), "1 &lt; 2 &amp; 3 &gt; 2")
        self.assertEqual(inline("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_a_star_between_words_is_not_emphasis(self):
        self.assertEqual(inline("2*3*4 and snake_case_name"), "2*3*4 and snake_case_name")

    def test_unsafe_link_targets_are_refused(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x/y", "vbscript:x"):
            with self.assertRaises(Unsupported):
                inline(f"[x]({bad})")
        for good in ("https://a.b/c", "http://a.b", "../x/", "/abs", "#anchor", "mailto:a@b.c"):
            inline(f"[x]({good})")


class Blocks(unittest.TestCase):
    def test_title_deck_headings_paragraphs(self):
        a = convert("# My Title\n\n*a subtitle*\n\n**TL;DR:** the short version.\n\n## First Part\n\nOne line\nsame paragraph.\n\n### Sub\n\nEnd.\n")
        self.assertEqual((a.title, a.deck), ("My Title", "a subtitle"))
        self.assertIn('<p class="tldr"><strong>TL;DR:</strong> the short version.</p>', a.body)
        self.assertIn('<h2 id="first-part">First Part</h2>', a.body)
        self.assertIn('<h3 id="sub">Sub</h3>', a.body)
        self.assertIn("<p>One line same paragraph.</p>", a.body)
        self.assertEqual(a.summary, "TL;DR: the short version.")
        self.assertNotIn("<h1", a.body)

    def test_an_italic_paragraph_later_in_the_text_is_not_taken_for_the_deck(self):
        a = convert("# T\n\nFirst paragraph.\n\n*Italic line.*\n")
        self.assertEqual(a.deck, "")
        self.assertIn("<p><em>Italic line.</em></p>", a.body)

    def test_lists_with_continuation_lines(self):
        a = convert("# T\n\n- first item\n  goes on here\n- **second** item\n\nafter\n")
        self.assertIn("<ul>\n<li>first item goes on here</li>\n<li><strong>second</strong> item</li>\n</ul>", a.body)
        self.assertIn("<p>after</p>", a.body)

    def test_code_fences_keep_text_verbatim_and_get_a_copy_button(self):
        a = convert('# T\n\n```python\nif a < b and "x" & y:\n    print(1)   # *not bold*\n```\n', copy_label="Copiar")
        self.assertIn('<pre><code class="language-python">if a &lt; b and "x" &amp; y:\n    print(1)   # *not bold*</code>', a.body)
        self.assertIn('<button class="copy" type="button" data-copy>Copiar</button></pre>', a.body)

    def test_comments_are_dropped_even_across_lines(self):
        a = convert("<!-- draft\n  note -->\n# T\n\ntext <!-- inline --> more\n")
        self.assertEqual(a.title, "T")
        self.assertNotIn("draft", a.body)
        self.assertIn("<p>text  more</p>", a.body)

    def test_words_exclude_code(self):
        a = convert("# T\n\none two three\n\n```\nfour five six seven\n```\n")
        self.assertEqual(a.words, 3)


class Refusals(unittest.TestCase):
    """Anything outside the supported subset must fail the build, not render wrongly."""

    def check(self, markdown, fragment):
        with self.assertRaises(Unsupported) as ctx:
            convert("# T\n\n" + markdown + "\n")
        self.assertIn(fragment, str(ctx.exception))

    def test_unsupported_constructs(self):
        self.check("> a quote", "block quotes")
        self.check("| a | b |\n|---|---|", "tables")
        self.check("1. one\n2. two", "numbered lists")
        self.check("![alt](x.png)", "images")
        self.check("<div>raw</div>", "raw HTML")
        self.check("---", "horizontal rules")
        self.check("* star list", "only '-' lists")
        self.check("#### too deep", "only '-' lists")

    def test_structure_errors(self):
        with self.assertRaises(Unsupported):
            convert("no title here\n")
        with self.assertRaises(Unsupported):
            convert("# One\n\n# Two\n")
        with self.assertRaises(Unsupported):
            convert("# T\n\n## Same\n\n## Same\n")
        with self.assertRaises(Unsupported):
            convert("# T\n\n```\nnever closed\n")

    def test_the_numbered_traps_style_is_fine(self):
        a = convert("# T\n\n**1. A trap.** Text.\n")
        self.assertIn("<p><strong>1. A trap.</strong> Text.</p>", a.body)


class RealArticle(unittest.TestCase):
    def test_the_published_article_converts(self):
        article = convert((ROOT / "articles" / "core-audio-tap-from-python.md").read_text(encoding="utf-8"))
        self.assertTrue(article.title and article.deck and article.summary.startswith("TL;DR"))
        self.assertGreater(article.words, 800)


if __name__ == "__main__":
    unittest.main()
