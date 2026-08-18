#!/usr/bin/env python3
"""
Translation file consistency check.

Loads every app/translations/*.json file and confirms:
  1. each one is valid JSON with only string leaf values (a stray number/
     list/null leaf is always a mistake - app/i18n.py's t() only ever
     returns strings)
  2. every key present in a non-English locale also exists in en.json -
     en.json is the canonical key set (see app/i18n.py); a key that only
     exists in, say, de.json is almost always a typo or a leftover from a
     since-renamed English key. Weblate's own consistency checks will
     later catch this too, but this script catches it in CI even without
     a reachable Weblate instance.

     Pluralized keys are compared with their CLDR suffix (_zero/_one/_two/
     _few/_many/_other) stripped first: a locale needing more plural
     categories than English legitimately has keys English doesn't (e.g.
     Ukrainian's "_few"/"_many" alongside English's "_one"/"_other" for
     the same pluralized string - see _plural_suffix() in app/i18n.py),
     and that's expected, not a typo.

This deliberately does NOT require every en.json key to have a translation
in every other locale - partial translation coverage is expected and
supported (t() falls back to English per-key, not per-file), not an error.

Usage: python scripts/check_translations.py [path/to/translations/dir]
Exit code 0 = clean, 1 = a problem was found.
"""
import json
import sys
from pathlib import Path


def flatten_keys(node, prefix=""):
    """Yields (dotted_key, value) for every leaf of a nested dict."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from flatten_keys(value, f"{prefix}{key}.")
        return
    yield prefix.rstrip("."), node


# Keep in sync with the suffixes _plural_suffix() in app/i18n.py can
# produce for any locale.
PLURAL_SUFFIXES = ("_zero", "_one", "_two", "_few", "_many", "_other")


def plural_base(key):
    """Strips a trailing CLDR plural suffix, if present - "foo_few" and
    "foo_one" both collapse to "foo" for comparison purposes, since
    they're different plural forms of the same pluralized string."""
    for suffix in PLURAL_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def main():
    translations_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "app/translations")
    paths = sorted(translations_dir.glob("*.json"))
    if not paths:
        print(f"No .json translation files found in {translations_dir}")
        sys.exit(1)

    problems = []
    locales = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            problems.append(f"{path.name}: invalid JSON: {e}")
            continue
        flat = dict(flatten_keys(data))
        for key, value in flat.items():
            if not isinstance(value, str):
                problems.append(f"{path.name}: key '{key}' is a {type(value).__name__}, not a string")
        locales[path.stem] = flat

    if "en" not in locales:
        problems.append("en.json is missing - it's the canonical key set every other locale is checked against")
    else:
        en_keys = set(locales["en"])
        en_plural_bases = {plural_base(k) for k in en_keys}
        for locale, flat in locales.items():
            if locale == "en":
                continue
            for key in sorted(set(flat) - en_keys):
                if plural_base(key) in en_plural_bases:
                    continue  # a plural form English doesn't need, e.g. Ukrainian's "_few"
                problems.append(f"{locale}.json: key '{key}' has no matching key in en.json")

    print(f"Checked {len(paths)} translation file(s) in {translations_dir}")
    if problems:
        print("PROBLEM:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)

    print("Translation check: clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
