"""Azure Function trigger definitions.

Each module here defines a `func.Blueprint()` for one or more triggers and imports
whatever it needs from `app/` (config, container, repositories) exactly like the root
`function_app.py` used to do inline. `function_app.py` (repo root, required there by
Azure Functions Core Tools) imports each blueprint from this package and registers it
onto the single `func.FunctionApp()` instance -- it does not define trigger bodies
itself anymore.
"""
