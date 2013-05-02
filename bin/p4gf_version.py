#! /usr/bin/env python3.2
'''Functions to implement Perforce's -V version string.'''

# Place imports for Perforce-related modules below the version check code
# found below, to avoid any spurious import errors.

# Python version is no longer explicityly checked in this module.
# Instead the SheBang on line 1 of scripts invokes the required python version.
# Thus, this module is no longer to be imported for python version checking.
# It IS to be imported into modules which  require its methods.

try:
    # pylint: disable=W0611
    import P4
except ImportError:
    print("Missing P4 Python module")
    exit(1)

# We're now assured of Python 3.2.x. But we don't actually REQUIRE 3.2
# for this file, it is OTHER files that require 3.2. They just use this
# file as a central place to enforce our system-wide 3.2 version
# requirements.


# Yeah we're importing *. Because we're the internal face for
# p4gf_version_26.py and I don't want ANYONE importing p4gf_version26.
#
# pylint: disable=W0401
# Wildcard import p4gf_version_26
#
# pylint: disable=W0614
# Unused import %s from wildcard import
from p4gf_version_26 import *

if __name__ == '__main__':
    print(as_string())
    exit(0)
