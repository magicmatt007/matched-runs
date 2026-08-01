#!/usr/bin/env python3
"""
Route/decorator safety check.

Catches a specific, easy-to-introduce bug: a helper function or constant
accidentally inserted between an @app.get/@app.post decorator and the
route function it's meant to decorate. Python doesn't consider this a
syntax error - the decorator just silently ends up applied to the wrong
function - so nothing catches it until the app crashes on startup with a
duplicate/missing route.

This is checked by looking for any app.<verb>(...) decorator attached to a
function whose name starts with an underscore - route handlers in this
codebase are never private-named, so a private-named function catching a
decorator like this is always a sign the decorator drifted onto the wrong
def during an edit. The only expected exception is the startup event
handler, which is intentionally private.

Usage: python scripts/check_routes.py [path/to/main.py]
Exit code 0 = clean, 1 = problem found.
"""
import ast
import sys

# The one legitimate case: FastAPI's startup event handler, which really
# is meant to be a private-named function.
EXPECTED_EXCEPTIONS = {("on_event", "_start_background_tasks")}


def check_file(path):
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)

    issues = []
    route_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
            ):
                continue
            route_count += 1
            if node.name.startswith("_") and (dec.func.attr, node.name) not in EXPECTED_EXCEPTIONS:
                issues.append((dec.func.attr, node.name))

    return route_count, issues


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "app/main.py"
    route_count, issues = check_file(path)

    print(f"Checked {route_count} routes in {path}")
    if issues:
        print("PROBLEM: the following route decorators are attached to an unexpectedly")
        print("private-named function - almost always means the decorator got separated")
        print("from its intended function by code inserted in between:")
        for decorator_name, func_name in issues:
            print(f"  @app.{decorator_name} -> def {func_name}")
        sys.exit(1)

    print("Route check: clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
