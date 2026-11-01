"""Repository-local operational tools used by the NEE runtime and test suite.

This marker prevents an inherited ``PYTHONPATH`` from resolving ``tools.*`` to
another checkout when the Decision Intelligence receipt runners import their
repository-local helpers.
"""
