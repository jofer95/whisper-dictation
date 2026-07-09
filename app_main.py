"""Bundle entry point for the .app (py2app runs this as the main script).

Kept as a thin wrapper so the CLI (`python -m dictation`) and the packaged app
share the exact same startup path.
"""

from dictation.__main__ import main

if __name__ == "__main__":
    main()
