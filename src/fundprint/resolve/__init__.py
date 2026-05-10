"""Resolve layer: fuzzy match + LLM extract + chain inference.

Public entrypoint is pipeline.run(). Other submodules (candidate, verify,
chain, embeddings) are importable for testing and for use in one-off scripts.
"""

from fundprint.resolve.pipeline import RunResult, run  # noqa: F401

__version__ = "0.1.0"
