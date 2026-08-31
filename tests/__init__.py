"""Test package.

``tests/eval/`` and ``tests/eval/regression/`` were already packages while this
file was missing, so ``from tests.eval.regression._vsphere_event_vocabulary
import ...`` resolved only where the namespace-package fallback happened to
work. It did not on Windows, and the whole suite -- 398 tests -- failed at
collection. The other repos that use cross-module test imports all carry this
file; this one is the half-built package.
"""
