"""A small Markdown-to-HTML converter for the site's own articles.

It handles exactly what they use (one # title, ## / ### headings, paragraphs, - lists with indented continuation lines,
fenced code, `code`, **bold**, *italic*, [links](url) and <!-- comments -->) and refuses everything else, so a construct
it does not understand fails the build instead of being rendered wrongly.
"""
import html
import re
from dataclasses import dataclass


class UnsupportedMarkdown(ValueError):
    pass


@dataclass
class Article:
    title: str
    deck: str          # the italic line under the title, as HTML ("" if none)
    body: str          # everything after the title and the deck, as HTML
    summary: str       # plain text of the TL;DR (or first) paragraph, for meta tags
    words: int         # words outside code blocks


SAFE_URL = re.compile(r"^(https?://|mailto:|/|\.{1,2}/|#)")


def slugify(text):
    text = re.sub(r"<[^>]+>|[`*_]", "", text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def inline(text):
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"
    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)

    def link(m):
        url = html.unescape(m.group(2))
        if not SAFE_URL.match(url):
            raise UnsupportedMarkdown(f"unsafe or unsupported link target: {url!r}")
        return f'<a href="{html.escape(url, quote=True)}">{m.group(1)}</a>'
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>" + html.escape(codes[int(m.group(1))], quote=False) + "</code>", text)


def plain(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def refuse(line, number):
    if re.match(r"\s*>", line):
        raise UnsupportedMarkdown(f"line {number}: block quotes are not supported")
    if line.lstrip().startswith("|"):
        raise UnsupportedMarkdown(f"line {number}: tables are not supported")
    if re.match(r"\s*\d+[.)]\s", line):
        raise UnsupportedMarkdown(f"line {number}: numbered lists are not supported (write the number inside bold text)")
    if re.match(r"\s*!\[", line):
        raise UnsupportedMarkdown(f"line {number}: images are not supported")
    if re.match(r"\s*<[a-zA-Z/]", line):
        raise UnsupportedMarkdown(f"line {number}: raw HTML is not supported")
    if re.match(r"\s*([-*_])\1{2,}\s*$", line):
        raise UnsupportedMarkdown(f"line {number}: horizontal rules are not supported")
    if re.match(r"\s*([*+]|#{4,})\s", line):
        raise UnsupportedMarkdown(f"line {number}: only '-' lists and #, ##, ### headings are supported")


def convert(markdown, copy_label="Copy"):
    md = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    lines = md.split("\n")
    out, title, deck, summary = [], None, "", None
    paragraph, items, ids = [], [], set()

    def flush_paragraph():
        nonlocal deck, summary
        if not paragraph:
            return
        text = " ".join(s.strip() for s in paragraph)
        paragraph.clear()
        rendered = inline(text)
        if title and not out and not deck and re.fullmatch(r"\*[^*].*[^*]\*", text):   # the line under the title
            deck = re.sub(r"^<em>(.*)</em>$", r"\1", rendered)
            return
        css = ' class="tldr"' if text.startswith("**TL;DR") else ""
        if summary is None and (css or title):
            summary = plain(rendered)
        out.append(f"<p{css}>{rendered}</p>")

    def flush_list():
        if items:
            out.append("<ul>\n" + "\n".join(f"<li>{inline(' '.join(i))}</li>" for i in items) + "\n</ul>")
            items.clear()

    i = 0
    while i < len(lines):
        line, n = lines[i], i + 1
        if line.startswith("```"):
            flush_paragraph(); flush_list()
            lang, code = line[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            if i >= len(lines):
                raise UnsupportedMarkdown(f"line {n}: code fence is never closed")
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code), quote=False)}</code>"
                       f'<button class="copy" type="button" data-copy>{copy_label}</button></pre>')
        elif not line.strip():
            flush_paragraph(); flush_list()
        elif re.match(r"#{1,3}\s", line):
            flush_paragraph(); flush_list()
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            if level == 1:
                if title is not None:
                    raise UnsupportedMarkdown(f"line {n}: only one # title is allowed")
                title = plain(inline(text))
            else:
                slug = slugify(text)
                if slug in ids:
                    raise UnsupportedMarkdown(f"line {n}: two headings would get the id {slug!r}")
                ids.add(slug)
                out.append(f'<h{level} id="{slug}">{inline(text)}</h{level}>')
        elif line.startswith("- "):
            flush_paragraph()
            items.append([line[2:]])
        elif items and line.startswith("  ") and line.strip():
            items[-1].append(line.strip())
        else:
            refuse(line, n)
            flush_list()
            paragraph.append(line)
        i += 1
    flush_paragraph(); flush_list()
    if title is None:
        raise UnsupportedMarkdown("the article needs a # title")
    body = "\n".join(out)
    words = len(plain(re.sub(r"<pre>.*?</pre>", " ", body, flags=re.S)).split())
    return Article(title, deck, body, summary or "", words)
