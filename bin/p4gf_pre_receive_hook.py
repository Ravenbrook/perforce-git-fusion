#! /usr/bin/env python3.2

# pylint:disable=C0301
# line too long? Too bad, that's what git sends us and I'm not hard-wrapping git sample data.

'''Called by git after 'git push' has transferred one or more commits
along with their tree and blob objects, but before git moves the head
pointer to the end of the newly transferred commits.

Pass control to p4gf_copy_to_p4.py to copy pending git commits into Perforce.
Fail with error if any git commit collides with a Perforce commit, git
user must pull, rebase, and re-attempt push.

This file must be copied or symlinked into .git/hooks/pre-receive
'''

import sys


import p4gf_context
from   p4gf_create_p4 import connect_p4
import p4gf_lock
import p4gf_log
import p4gf_util
import p4gf_version
import p4gf_copy_to_p4
LOG = p4gf_log.for_module()


def _copy(ctx, old_sha1, new_sha1, ref):
    """Copy a sequence of commits from git to Perforce.

    -> ref      what's being pushed, usually refs/heads/master.
    -> old_sha1 the old value of that ref, a commit that already existed
                the repo before this push.
    -> new_sha1 the new value of that ref, not yet assigned, but which
                points to a sequence of commits that reach back to
                old_sha1, and which will become the new value for ref
                soon after we return.
    """

    LOG.debug("copy old={old} new={new} ref={ref}".format(old=old_sha1,
                                                          new=new_sha1,
                                                          ref=ref))

    # Only master earns a spot in Perforce. All other branches stay in Git.
    if ref != 'refs/heads/master':
        return

    p4gf_copy_to_p4.copy_git_changes_to_p4(ctx, old_sha1, new_sha1)


def main():
    """create Perforce user and client for Git Fusion"""
    p4gf_version.print_and_exit_if_argv()
    p4gf_util.reset_git_enviro()

    p4 = connect_p4()
    if not p4:
        return 2

    view_name = p4gf_util.cwd_to_view_name()
    view_lock = p4gf_lock.view_lock_heartbeat_only(p4, view_name)
    ctx       = p4gf_context.create_context(view_name, view_lock)

    # Read each input line (usually only one unless pushing multiple branches)
    # and pass to git-to-p4 copier.
    while True:
        line = sys.stdin.readline()
        if not line:
            break

        old_new_ref = line.strip().split()
        try:
            _copy( ctx
                 , old_sha1     = old_new_ref[0]
                 , new_sha1     = old_new_ref[1]
                 , ref          = old_new_ref[2])
        except RuntimeError as err:
            # bleed the input
            sys.stdin.readlines()
            # display the error message
            print(str(err))
            return 1
    return 0

if __name__ == "__main__":
    p4gf_log.run_with_exception_logger(main, write_to_stderr=True)
