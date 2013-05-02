#! /usr/bin/env python3.2

"""Path utilities broken out of p4gf_util's kitchen sink."""

import os

import p4gf_const

def cwd_to_dot_git():
    """If cwd or one of its ancestors is a .git return that .git directory.

    If not, return None.
    """
    path = os.getcwd()
    while path:
        (path2, tail) = os.path.split(path)
        if path2 == path:
            # Give up once split() stops changing the path: we've hit root.
            break
        path = path2
        if tail == '.git':
            return os.path.join(path, tail)
    return None

def cwd_to_rc_file():
    """If cwd or one of its ancestors contains a .git-fusion-rc file,
    return the path to that rc file. If not, return None.
    """
    path = os.getcwd()
    while path:
        rc = os.path.join(path, p4gf_const.P4GF_RC_FILE)
        if os.path.exists(rc):
            return rc

        (path2, _tail) = os.path.split(path)
        if path2 == path:
            # Give up once split() stops changing the path: we've hit root.
            break
        path = path2
    return None


def find_ancestor(path, ancestor):
    """Walk up the path until you find a dir named 'ancestor', and return
    the path to that ancestor.

    Return None if no ancestor called 'ancestor'.
    """
    path = path
    while path:
        (path2, tail) = os.path.split(path)
        if path2 == path:
            # Give up once split() stops changing the path: we've hit root.
            break

        if tail == ancestor:
            return path

        path = path2
    return None


def dequote(path):
    """Strip leading and trailing double-quotes if both present, NOP if not."""
    if (2 <= len(path)) and path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


def enquote(path):
    """Paths with space char require double-quotes, all others pass
    through unchanged.
    """
    if ' ' in path:
        return '"' + path + '"'
    return path


def slash_dot_dot_dot(path):
    """Return //path/...

    This is not a full path manipulation suite, you'll get
    double-slashes if <path> starts or ends with slashes, so don't do
    that (or port more path manipulation code to Python.
    """
    s = path
    if not path.startswith("//"):
        s = "//" + s
    if not path.endswith("/..."):
        s = s + "/..."
    return s


