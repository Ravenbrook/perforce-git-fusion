#! /usr/bin/env python3.2
"""Copy change history from Perforce to git.

View must already be defined in Perforce: it must have its
"git-fusion-<view>" client with a Root and a View.

Git repo must already be inited in view_dirs.GIT_DIR.
The repo can be empty.
"""

import shutil
import os
import sys


import p4gf_const
import p4gf_copy_to_git
import p4gf_log
import p4gf_path
import p4gf_util
import p4gf_view_dirs

LOG = p4gf_log.for_module()

def _p4_empty(ctx):
    """
    Is our client view completely empty, no files, not even deleted or purged?
    """
    r = ctx.p4.run('files', '-m1', p4gf_path.slash_dot_dot_dot(ctx.config.p4client))
    return not r


def _git_empty():
    """
    Is our git repo completely empty, not a single commit?
    """
    ### Replace git log with git rev-list.
    p = p4gf_util.popen_no_throw(['git', 'log', '-1', '--oneline'])
    return not p['out']

def copy_p2g(ctx, start):
    """Fill git with content from Perforce."""

    view_name = ctx.config.view_name
    view_dirs = ctx.view_dirs
    git_dir = view_dirs.GIT_DIR
    if not os.path.exists(git_dir):
        LOG.warn("mirror Git repository {} missing, recreating...".format(git_dir))
        # it's not the end of the world if the git repo disappears, just recreate it
        create_git_repo(git_dir)

    # If Perforce client view is empty and git repo is empty, someone is
    # probably trying to push into an empty repo/perforce tree. Let them.
    if _p4_empty(ctx) and _git_empty():
        LOG.info("Nothing to copy from empty view {}".format(view_name))
        return

    # We're not empty anymore, we no longer need this to avoid
    # git push rejection of push to empty repo refs/heads/master.
    delete_empty_repo_branch(view_dirs.GIT_DIR)

    start_at = p4gf_util.git_ref_master()
    if start and start_at:
        raise RuntimeError((  "Cannot use --start={start} when repo already has commits."
                            + " master={start_at}")
                           .format(start=start, start_at=start_at))
    if start:
        start_at = "@{}".format(start)
    elif start_at is None:
        start_at = "@1"

    p4gf_copy_to_git.copy_p4_changes_to_git(ctx, start_at, "#head")

    # Want to exit this function with HEAD and master both pointing to
    # the end of history. If we just copied anything from Perforce to
    # Git, point to the end of Perforce history.
    #
    # Want to leave this function with our temp branch gone. Otherwise
    # pullers will see "origin/git_fusion_temp_branch" and wonder "if
    # it's temp, why does it seem to live forever?"

    # Common: We have a temp branch that has added zero or more
    # commits ahead of master. Move HEAD and master to the temp branch's
    # commit. Move HEAD, detached (~0), first, just in case someone left it on master.
    temp_branch = p4gf_const.P4GF_BRANCH_TEMP + '~0'
    p1 = p4gf_util.popen_no_throw(['git', 'checkout', temp_branch])
    detached_head = (p1['Popen'].returncode == 0)
    if detached_head:
        p4gf_util.popen_no_throw(['git', 'branch', '-f', 'master', temp_branch])

    # Rare: If there are zero p4 changes in this view (yet), our temp
    # branch either does not exist or points nowhere and we were unable
    # to detach head from that temp branch. In that case switch to
    # (empty) branch master, creating it. We really want a master
    # branch, even if empty, so that we can delete the temp branch.
    if not detached_head:
        p4gf_util.popen_no_throw(['git', 'checkout', '-b', 'master'])

    p4gf_util.popen_no_throw(['git', 'branch', '-d', p4gf_const.P4GF_BRANCH_TEMP])


def create_empty_repo_branch(git_dir):
    '''
    Create and switch to branch empty_repo.

    This avoids Git errors when pushing to a brand-new empty repo which
    prohibits pushes to master.

    We'll switch to master and delete this branch later, when there's
    something in the repo and we can now safely detach HEAD from master.
    '''
    for branch in ['master', p4gf_const.P4GF_BRANCH_EMPTY_REPO]:
        p4gf_util.popen(['git', '--git-dir=' + git_dir, 'checkout', '-b', branch])


def delete_empty_repo_branch(git_dir):
    '''
    Delete branch empty_repo. If we are currently on that branch,
    detach head before switching.

    Only do this if our HEAD points to an actual sha1: we have to have
    at least one commit.
    '''
    p4gf_util.popen_no_throw(['git', '--git-dir=' + git_dir, 'checkout', '-b', 'master'])
    p = p4gf_util.popen_no_throw(['git', '--git-dir=' + git_dir, 'branch', '--list',
            p4gf_const.P4GF_BRANCH_EMPTY_REPO])
    if p['out']:
        p = p4gf_util.popen_no_throw(['git', '--git-dir=' + git_dir, 'branch', '-D',
                p4gf_const.P4GF_BRANCH_EMPTY_REPO])


def create_git_repo(git_dir):
    """Create the git repository in the given root directory."""

    # Test if the Git repository has already been created.
    if os.path.exists(os.path.join(git_dir, 'HEAD')):
        return

    # Prepare the Git repository directory, cleaning up if necessary.
    if not os.path.exists(git_dir):
        parent = os.path.dirname(git_dir)
        if os.path.exists(parent):
            # weird case where git view dir exists but repo was deleted
            LOG.warn("mirror Git repository {} in bad state, repairing...".format(git_dir))
            shutil.rmtree(parent)
        LOG.debug("creating directory %s for Git repo", git_dir)
        os.makedirs(git_dir)

    # Initialize the Git repository for that directory.
    LOG.debug("creating Git repository in %s", git_dir)
    cmd = ['git', '--git-dir=' + git_dir, 'init']
    result = p4gf_util.popen_no_throw(cmd)
    if result['Popen'].returncode:
        code = result['Popen'].returncode
        LOG.error("error creating Git repo, git init returned %d", code)
        sys.stderr.write("error: git init failed with {} for {}\n".format(code, git_dir))

    create_empty_repo_branch(git_dir)


def copy_p2g_ctx(ctx, start=None):
    """Using the given context, copy its view from Perforce to Git.

    Common code for p4gf_auth_server.py and p4gf_init_repo.py for setting up
    the eventual call to copy_p2g."""

    view_name = ctx.config.view_name

    # Find directory paths to feed to git.
    p4gf_dir = p4gf_util.p4_to_p4gf_dir(ctx.p4gf)
    ctx.view_dirs = p4gf_view_dirs.from_p4gf_dir(p4gf_dir, view_name)

    # cd into the work directory. Not all git functions react well to --work-tree=xxxx.
    os.chdir(ctx.view_dirs.GIT_WORK_TREE)

    # Fill git with content from Perforce.
    copy_p2g(ctx, start)
