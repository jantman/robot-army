"""Marks ``tests/unit`` as a package.

Present only so that two test modules may share a basename across ``tests/unit`` and
``tests/integration`` — ``test_cleanup.py`` does, because milestone 004's cleanup has both
a decision table worth unit-testing and a pass worth driving against real git, and naming
one of them something else to satisfy the import machinery would misname it.
"""
