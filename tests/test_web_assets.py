"""Static checks on the web files. These read the source, not a rendered page: the rendered behaviour (what shows at which
width, scrollWidth) is checked with a browser by hand, because the project has no browser test runner."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def read(name):
    return (WEB / name).read_text()


def test_status_has_an_accessible_name_the_dot_alone_can_carry():
    tag = re.search(r'<div[^>]*id="status"[^>]*>', read("index.html")).group(0)
    assert 'role="img"' in tag and 'aria-label="Backend status' in tag      # aria-label on a bare div isn't announced without a role


def test_small_screens_hide_the_status_text_visually_but_keep_it_in_the_page():
    css = read("style.css")
    block = re.search(r"@media \(max-width:479px\)\{(.*?)\n\}", css, re.S).group(1)
    rule = re.search(r"#statusText\{([^}]*)\}", block).group(1)
    assert "clip" in rule and "position:absolute" in rule and "display:none" not in rule
    assert not re.search(r"#statusText\{[^}]*ellipsis", css)                 # the old truncation hack is gone


def test_every_status_update_goes_through_one_helper_so_the_label_cannot_go_stale():
    js = read("app.js")
    assert "setStatus('ok'" in js and "setStatus('err'" in js
    assignments = re.findall(r"statusText'\)\.(?:textContent|innerHTML)\s*=", js)
    assert len(assignments) == 1                                            # only inside setStatus
    helper = re.search(r"function setStatus\(.*?\n\}", js, re.S).group(0)
    assert "aria-label" in helper and "title" in helper
