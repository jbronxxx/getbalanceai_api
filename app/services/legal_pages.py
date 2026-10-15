"""Рендеринг публичных юридических страниц (политика, соглашение, удаление аккаунта) в HTML."""

from html import escape

from app.legal import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    PAGES,
    SUPPORT_EMAIL,
    UI_STRINGS,
    Block,
)

# Порядок страниц в навигации футера
_NAV_ORDER = ("privacy", "terms", "delete-account")

_STYLE = """
:root{--brand:#0D9488;--bg:#f0fdfa;--text:#134e4a;--card:#fff;--muted:#5f7f7b}
@media(prefers-color-scheme:dark){:root{--bg:#0b1514;--text:#d7efec;--card:#13211f;--muted:#8fb3af}}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
header{background:var(--brand);color:#fff;padding:24px 16px;text-align:center}
header h1{margin:0;font-size:1.5rem}
main{max-width:760px;margin:0 auto;padding:16px}
section,.panel{background:var(--card);border-radius:12px;padding:20px;margin:16px 0;
box-shadow:0 1px 4px rgba(13,148,136,.15)}
h2{color:var(--brand);margin-top:0;font-size:1.15rem}
a{color:var(--brand);font-weight:600}
ul{padding-left:20px}
.meta{color:var(--muted);font-size:.9rem;margin:8px 4px}
.langs,.links{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
.langs a[aria-current]{text-decoration:none;color:var(--text)}
@media(max-width:480px){header h1{font-size:1.25rem}}
"""


def resolve_language(lang: str | None, accept_language: str | None) -> str:
    """Определить язык страницы: параметр `lang`, затем заголовок Accept-Language, иначе язык по умолчанию."""
    if lang and lang.strip().lower() in LANGUAGES:
        return lang.strip().lower()

    candidates: list[tuple[float, str]] = []
    for part in (accept_language or "").split(","):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                quality = 0.0
        code = tag.strip().split("-")[0].lower()
        if code in LANGUAGES:
            candidates.append((quality, code))

    if candidates:
        # sorted стабилен: при равном q сохраняется порядок в заголовке
        return sorted(candidates, key=lambda item: -item[0])[0][1]
    return DEFAULT_LANGUAGE


def _render_block(block: Block) -> str:
    """Отрисовать абзац или маркированный список."""
    if isinstance(block, str):
        return f"<p>{escape(block)}</p>"
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in block) + "</ul>"


def render_page(page: str, lang: str) -> str:
    """Собрать HTML-страницу `page` на языке `lang` (оба значения должны быть из реестра PAGES / LANGUAGES)."""
    content = PAGES[page][lang]
    ui = UI_STRINGS[lang]
    title = escape(content["title"])
    mail = escape(SUPPORT_EMAIL)

    sections = "".join(
        f"<section><h2>{escape(heading)}</h2>{''.join(_render_block(b) for b in blocks)}</section>"
        for heading, blocks in content["sections"]
    )
    current = ' aria-current="true"'
    lang_links = " ".join(
        f'<a href="?lang={code}" hreflang="{code}"{current if code == lang else ""}>{escape(name)}</a>'
        for code, name in LANGUAGES.items()
    )
    page_links = " ".join(f'<a href="/{slug}?lang={lang}">{escape(ui[slug])}</a>' for slug in _NAV_ORDER)

    return (
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"<header><h1>{title}</h1></header><main>"
        f'<nav class="panel langs" aria-label="{escape(ui["language"])}"><strong>{escape(ui["language"])}:</strong> '
        f"{lang_links}</nav>"
        f'<p class="meta">{escape(ui["updated"])}</p>'
        f"{sections}"
        f'<section><p>{escape(ui["contact"])}: <a href="mailto:{mail}">{mail}</a></p></section>'
        f'<nav class="panel links">{page_links}</nav>'
        "</main></body></html>"
    )
