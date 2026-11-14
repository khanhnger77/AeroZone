"""Odyssey Copilot: conversational assistant for the MARS ODYSSEY planner.

This file makes `odyssey_copilot` a package. Without it, pytest imports
`odyssey_copilot/backend` as a top-level package named `backend`, which shadows the
route planner's `backend` package (M2) and breaks `import backend.main` in tests/.
"""
