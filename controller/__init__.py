"""The agents that drive the git-backed memory.

A Worker changes code, the test suite judges it, and a Thinker decides which
commit the next attempt starts from. The memory keeps every attempt, including
the ones that failed, because those are what the Thinker reasons over.
"""

from controller.loop import Controller

__all__ = ["Controller"]
