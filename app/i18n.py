"""
Minimal i18n layer: nested per-locale JSON files, resolved per-request from
the Accept-Language header (works identically standalone and behind Home
Assistant's ingress proxy - ingress forwards standard headers through
rather than replacing them, so no HA-specific mechanism is needed here).

File format is deliberately the "i18next JSON" convention (nested keys,
CLDR plural-category suffixes like `_one`/`_other` on pluralized keys)
rather than a flat/ad-hoc shape, because that's the format the self-hosted
Weblate workflow this app is meant to feed has first-class support for -
see the versioned discussion in the project's chat history for why plain
gettext .po/.mo wasn't used instead (no compile step, stays plain JSON).

en.json is the canonical key set - every other locale falls back to it,
per-key, for anything untranslated. That fallback (not "must be 100%
translated to appear") is what lets a locale be added incrementally.
"""
import json
import os
from pathlib import Path

from fastapi import Request

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"


def _load_translations() -> dict:
    translations = {}
    for path in sorted(_TRANSLATIONS_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            translations[path.stem] = json.load(f)
    return translations


# Loaded once at import time, like the rest of this app's startup-time
# state (matches Base.metadata.create_all/run_migrations in main.py) -
# translation files change only on deploy, never at runtime.
_translations = _load_translations()
SUPPORTED_LOCALES = sorted(_translations)

LOCALE_COOKIE_NAME = "locale"

# A language's own name, shown in itself rather than translated into
# whatever locale happens to be active right now - how every language
# picker does it (nobody wants to hunt for their language spelled in a
# language they don't read). Keep in sync with SUPPORTED_LOCALES; a
# locale missing here just falls back to showing its bare code.
LOCALE_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "uk": "Українська",
}


def locale_name(code: str) -> str:
    return LOCALE_NAMES.get(code, code)


def cookie_locale(request: Request) -> str:
    """The raw locale cookie value, unvalidated and possibly empty/unset -
    used by the language-picker UI to know which option to preselect.
    Unlike resolve_locale(), this doesn't fall back to anything; a blank
    result means "no explicit per-browser choice made yet", which the
    picker renders as "Auto"."""
    return request.cookies.get(LOCALE_COOKIE_NAME, "")


def _lookup(dotted_key: str, locale: str):
    """Nested dict lookup for a "section.subsection.key"-style key.
    Returns None (rather than raising) on any miss, so callers can chain
    fallbacks cheaply instead of catching KeyError at every level."""
    node = _translations.get(locale)
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def _plural_suffix(count: int, locale: str) -> str:
    """CLDR plural category for `count`, as an i18next-style key suffix.

    Most locales so far (English/German-family) only need the "one"/
    "other" split below. A locale needing more CLDR categories (Arabic's
    zero/two/few/many, ...) needs its own branch added here *before* it's
    added to app/translations/, or its plural keys will silently fall back
    to "_other" for every count. Not backed by a CLDR library on purpose
    (see the module docstring); extend this table as real locales are
    added rather than pulling one in speculatively.
    """
    if locale == "uk":
        # Ukrainian (like Russian/Polish/other Slavic languages) has four
        # categories - standard CLDR rule, keyed off the last one/two
        # digits of the count: 1, 21, 31, ... -> one; 2-4, 22-24, ... ->
        # few (but not the 12-14 teens, which fall to "many" instead);
        # everything else (0, 5-9, 11-14, 20, 25, ...) -> many. "other" is
        # CLDR's catch-all for non-integer values (e.g. 1.5) - unreachable
        # here since `count` is always a whole number, but still needs a
        # translated string for completeness (Weblate's editor shows an
        # input for it regardless).
        n10, n100 = count % 10, count % 100
        if n10 == 1 and n100 != 11:
            return "_one"
        if 2 <= n10 <= 4 and not (12 <= n100 <= 14):
            return "_few"
        if n10 == 0 or 5 <= n10 <= 9 or 11 <= n100 <= 14:
            return "_many"
        return "_other"
    return "_one" if count == 1 else "_other"


def resolve_locale(request: Request) -> str:
    """Picks the best supported locale for this request, most specific
    choice first:

    1. The locale cookie - an explicit, per-browser choice made via the
       language picker in the nav bar (see /set-locale in main.py).
       Deliberately checked before the instance-wide LOCALE override
       below: a household with two people using two languages should
       each get their own, without one overriding the other.
    2. LOCALE, if set - pins the instance's *default* language regardless
       of the browser, for anyone who hasn't made an explicit in-app
       choice yet. Same override-a-setting pattern the rest of this app's
       config already follows (see docker_entrypoint.py). Originally
       added for Home Assistant's ingress case, where this app has no way
       to see the language you picked *inside* HA (see config.yaml) - the
       cookie-based picker above covers that same case now too, and works
       standalone as well, but this is kept as a deployment-wide default.
    3. Accept-Language (q-value aware) - the browser's own setting.
    4. English.
    """
    cookie = request.cookies.get(LOCALE_COOKIE_NAME)
    if cookie in _translations:
        return cookie

    override = os.environ.get("LOCALE")
    if override in _translations:
        return override

    header = request.headers.get("accept-language", "")
    ranked = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        if ";q=" in part:
            tag, q = part.split(";q=", 1)
            try:
                q = float(q)
            except ValueError:
                q = 1.0
        else:
            tag, q = part, 1.0
        # "de-DE" -> "de": this app matches on base language only, it
        # doesn't (yet) distinguish regional variants.
        ranked.append((tag.strip().split("-")[0].lower(), q))

    for code, _ in sorted(ranked, key=lambda pair: pair[1], reverse=True):
        if code in _translations:
            return code
    return "en"


def t(request: Request, key: str, count: int = None, **kwargs) -> str:
    """Looks up `key` (dotted path into the locale's JSON) in the request's
    resolved locale, falling back to English, then to the key itself -
    the last resort is deliberately loud/ugly rather than a blank label,
    so a missed extraction is obvious instead of silently invisible.

    Pass `count=` for a pluralized key (defined in JSON as `<key>_one`,
    `<key>_other`, ...); it's also made available to `.format()` as
    `{count}` automatically. Any other kwargs are passed through to
    `.format()` for the rest of the string's placeholders.
    """
    locale = resolve_locale(request)
    lookup_key = f"{key}{_plural_suffix(count, locale)}" if count is not None else key

    text = _lookup(lookup_key, locale)
    if text is None and locale != "en":
        text = _lookup(lookup_key, "en")
    if text is None and count is not None:
        # No plural form defined at all (yet) for this key - fall back to
        # the bare key rather than failing outright.
        text = _lookup(key, locale) or _lookup(key, "en")
    if text is None:
        return key

    if count is not None:
        kwargs.setdefault("count", count)
    return text.format(**kwargs) if kwargs else text
