#! /usr/bin/env python3.2
"""
A collection of useful directory and file paths.
"""
import os
import p4gf_const

# pylint: disable=C0103
# C0103 Invalid name
# Yeah, because it's a git environment var, and it is correctly spelled in all caps.

class ViewDirs:
    """Paths to various directories and files."""

    def __init__(self):
        self.p4gf_dir         = None # ~/.git-fusion
        self.view_container = None # ~/.git-fusion/views/<view>
        self.rcfile         = None # ~/.git-fusion/views/<view>/.git-fusion-rc
        self.GIT_WORK_TREE  = None # ~/.git-fusion/views/<view>/git
        self.GIT_DIR        = None # ~/.git-fusion/views/<view>/git/.git
        self.p4root         = None # ~/.git-fusion/views/<view>/p4
                                   #    (client git-fusion-<view>'s Root)

def from_p4gf_dir(p4gf_dir, view_name):
    """Return a dict of calculated paths where a view's files should go.

    Does not check for existence.
    """
    if not p4gf_dir:
        raise RuntimeError("Empty p4gf_dir")
    if not view_name:
        raise RuntimeError("Empty view_name")

    view_container = os.path.join(p4gf_dir, "views", view_name)
    view_dirs = ViewDirs()
    view_dirs.p4gf_dir         = p4gf_dir
    view_dirs.view_container = view_container
    view_dirs.rcfile         = os.path.join(view_container, p4gf_const.P4GF_RC_FILE)
    view_dirs.GIT_WORK_TREE  = os.path.join(view_container, "git")
    view_dirs.GIT_DIR        = os.path.join(view_container, "git", ".git")
    view_dirs.p4root         = os.path.join(view_container, "p4")
    return view_dirs
