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
    block = next(b for b in re.findall(r"@media \(max-width:479px\)\{(.*?)\n\}", css, re.S) if "#statusText" in b)
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


# ---------------------------------------------------------------- evaluation graph colours (WCAG maths on the CSS tokens)
def tokens(selector_re):
    """The --g-* custom properties declared in the first rule whose selector matches."""
    css = read("style.css")
    body = re.search(selector_re + r"\s*\{(.*?)\}", css, re.S).group(1)
    return dict(re.findall(r"(--g-[\w-]+)\s*:\s*([^;]+);?", body))


DARK = lambda: tokens(r"(?m)^:root")
LIGHT = lambda: tokens(r":root\[data-theme=light\]")


def luminance(hex_):
    r, g, b = (int(hex_.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_dark_graph_white_side_is_dimmed_so_it_does_not_dominate_the_page():
    t = DARK()
    assert luminance(t["--g-white"]) <= 0.5                                 # the old #f4f5f8 is about 0.91
    assert contrast(t["--g-white"], t["--g-black"]) >= 3                    # but still clearly the lighter side


def test_dark_graph_centre_line_shows_on_both_sides():
    t = DARK()
    assert contrast(t["--g-mid"], t["--g-black"]) >= 3                      # light core on the dark side
    assert contrast(t["--g-mid-casing"], t["--g-white"]) >= 3               # dark casing on the light side
    assert contrast(t["--g-mid"], t["--g-mid-casing"]) >= 3                 # and core against its own casing


def test_light_theme_graph_keeps_its_original_colours():
    t = LIGHT()
    assert (t["--g-white"], t["--g-black"], t["--g-mid"]) == ("#f4f5f8", "#1e222d", "#7d88aa")


def test_graph_colours_come_from_css_not_hardcoded_in_the_drawing_code():
    js = read("app.js")
    body = re.search(r"function renderGraph\(\).*?\n\}", js, re.S).group(0)
    assert not re.search(r'#(?:f4f5f8|1e222d|7d88aa)', body, re.I)
    assert all(c in body for c in ("g-white", "g-black", "g-mid-casing", "g-rim"))


# ---------------------------------------------------------------- the home page preview of a finished review
def test_home_preview_follows_the_import_form_and_is_described_for_screen_readers():
    html = read("index.html")
    assert html.index('class="import card"') < html.index('class="preview') < html.index('id="libraryCard"')
    tag = re.search(r'<div class="pv"[^>]*>', html).group(0)
    assert 'role="img"' in tag and re.search(r'aria-label="[^"]{30,}"', tag)


def preview_files():
    """(dark, light, mobile dark, mobile light), as named by the CSS rules that choose between them."""
    css = read("style.css")
    dark = re.search(r"\.preview \.pv\{[^}]*url\(/assets/([^)]+)\)", css).group(1)
    light = re.search(r"data-theme=light\] \.preview \.pv\{background-image:url\(/assets/([^)]+)\)", css).group(1)
    phone = re.search(r"@media \(max-width:479px\)\{[^@]*?\.preview \.pv\{[^}]*url\(/assets/([^)]+)\)[^@]*?data-theme=light\] \.preview \.pv\{background-image:url\(/assets/([^)]+)\)", css, re.S)
    return dark, light, phone.group(1), phone.group(2)


def test_each_theme_and_screen_size_gets_its_own_image_through_one_css_rule_so_only_one_is_downloaded():
    files = preview_files()
    assert len(set(files)) == 4
    css = read("style.css")
    assert "aspect-ratio:1128/940" in css and "aspect-ratio:780/1560" in css      # reserves the space: no layout shift
    assert "<img" not in re.search(r'<figure class="preview.*?</figure>', read("index.html"), re.S).group(0)   # an <img> would fetch both


def test_preview_images_are_real_small_webp_files_that_git_will_keep():
    import subprocess
    for name in preview_files():
        data = (WEB / name).read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) <= 150_000
        # .gitignore and .vercelignore both drop *.png, which is why these are WebP
        assert subprocess.run(["git", "check-ignore", "-q", str(WEB / name)], cwd=WEB).returncode == 1
        assert not any(l.strip().startswith("*.webp") for l in (WEB.parent / ".vercelignore").read_text().splitlines())
