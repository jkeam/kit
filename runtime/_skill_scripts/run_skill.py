"""Fixed helper script for skill execution.

Runs a skill's code via exec() inside its own subprocess, isolated from the
gateway process (separate memory space, killed cleanly on timeout, crashes
can't take down the server). Code and arguments arrive as JSON on stdin
rather than being interpolated into a script string, and the restricted
builtins/import allowlist lives in runtime.skills as the single source of
truth.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from runtime.skills import _build_restricted_namespace  # noqa: E402


def main() -> None:
    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    func_name = payload["func_name"]
    kwargs = payload.get("kwargs", {})

    try:
        namespace = _build_restricted_namespace()
        exec(code, namespace)

        func = namespace.get(func_name) or namespace.get("main")
        if not func:
            print(json.dumps({
                "error": f"No function '{func_name}' or 'main' found in skill"
            }))
            return

        result = func(**kwargs)
        print(json.dumps({"result": str(result)}))

    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    main()
