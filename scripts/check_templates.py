#!/usr/bin/env python3
"""
Jinja2 template syntax check.

Loading (not rendering) every template catches malformed template syntax -
unclosed {% %} / {{ }} blocks, mismatched {% if %}/{% endif %}, etc. - that
nothing else in this checklist would catch, since templates aren't Python
and py_compile never touches them.

This deliberately does NOT render the templates (that would need realistic
fake data for every page, which is a lot of upkeep for a CI check) - it
only confirms each one is syntactically valid Jinja2.

Usage: python scripts/check_templates.py [path/to/templates/dir]
Exit code 0 = clean, 1 = a template failed to parse.
"""
import re
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError


def discover_custom_filters(main_py_path="app/main.py"):
    """Finds every templates.env.filters["name"] = ... registration in
    main.py, so this check stays in sync automatically if a new filter
    gets added later, instead of silently going stale."""
    try:
        with open(main_py_path) as f:
            source = f.read()
    except FileNotFoundError:
        return []
    return re.findall(r'templates\.env\.filters\[["\']([^"\']+)["\']\]\s*=', source)


def main():
    templates_dir = sys.argv[1] if len(sys.argv) > 1 else "app/templates"
    env = Environment(loader=FileSystemLoader(templates_dir))

    # Stand-ins for whatever custom filters app/main.py registers on its
    # real Jinja2Templates instance - Jinja validates that a referenced
    # filter exists even during a parse-only load, so without these every
    # template using them would fail here with a false "no filter named
    # ..." error. The actual implementation doesn't matter, since this
    # script only parses templates, never renders them.
    for filter_name in discover_custom_filters():
        env.filters[filter_name] = lambda *a, **k: ""

    template_files = sorted(Path(templates_dir).glob("*.html"))
    if not template_files:
        print(f"No .html templates found in {templates_dir}")
        sys.exit(1)

    failures = []
    for path in template_files:
        try:
            env.get_template(path.name)
        except TemplateSyntaxError as e:
            failures.append((path.name, e))

    print(f"Checked {len(template_files)} templates in {templates_dir}")
    if failures:
        print("PROBLEM: the following templates failed to parse:")
        for name, err in failures:
            print(f"  {name}: line {err.lineno}: {err.message}")
        sys.exit(1)

    print("Template check: clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
