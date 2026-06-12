from sys import version_info

if version_info >= (3, 9):
    from .py_3_9 import *  # noqa F403
else:
    from .py_3_8 import *  # noqa F403
