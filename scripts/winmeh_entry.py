"""PyInstaller entry point. Logs any crash instead of showing a blocking dialog."""
import os
import sys
import traceback

if __name__ == "__main__":
    try:
        from winmeh.__main__ import main
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WinMeh")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "crash.log"), "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        sys.exit(1)
