import requests
from fakes import story

from newsroom.article import extract_text, read_article

PAGE = """<html><head><title>Acme ships Model X</title></head><body>
<nav>Home | Pricing | Login | Subscribe to our newsletter</nav>
<article>
  <h1>Acme ships Model X</h1>
  <p>Acme released Model X on Monday, its first open-weights model for code. The company says it beats its
  previous model by seven points on a popular coding benchmark while running on a single GPU.</p>
  <p>Model X is free for personal and research use. Businesses with more than 100 employees need a license,
  according to the announcement published on the company blog this morning.</p>
  <p>Early testers reported faster responses on long files, though the model still struggles with some
  older languages. Acme plans a larger version early next year.</p>
</article>
<footer>© 2026 Acme. Cookie settings. Privacy policy.</footer>
</body></html>"""


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, resp=None, error=None):
        self.resp, self.error, self.calls = resp, error, 0

    def get(self, url, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.resp


def test_extract_text_keeps_the_article_and_drops_page_chrome():
    text = extract_text(PAGE)
    assert "Model X is free for personal and research use" in text
    assert "Cookie settings" not in text and "Subscribe to our newsletter" not in text


def test_read_article_uses_the_page():
    text, where = read_article(story(1), Session(Resp(PAGE)))
    assert where == "article" and "single GPU" in text


def test_read_article_falls_back_to_feed_on_errors():
    s = story(1, summary="Feed text.")
    assert read_article(s, Session(error=requests.ConnectionError("down"))) == ("Feed text.", "feed")
    assert read_article(s, Session(Resp("", status=403))) == ("Feed text.", "feed")


def test_read_article_skips_arxiv():
    session = Session(Resp(PAGE))
    s = story(1, "arXiv cs.CL", domain="arxiv.org", summary="We propose a method.")
    assert read_article(s, session) == ("We propose a method.", "feed (abstract)")
    assert session.calls == 0
