#! /usr/bin/env python3.2
'''p4gf_init_repo.py [--start nnn] <view>

Create config data to map <view> to a Git Fusion client,
local filesystem location for .git and workspace data.

<view> must be an existing Perforce client spec. Its view mapping is copied
into the git repo's config. After p4gf_init_repo.py completes, Git Fusion no
longer uses or needs this <view> client spec, you can delete it or use it for
your own purposes. We just needed to copy its view mapping once. Later changes
to this view mapping are NOT propagated to Git Fusion. <view> cannot be a
stream client.

p4gf_init_repo.py creates a new Perforce client spec 'git-fusion-<view>'. This
is the client for this view, which Git Fusion uses for all operations
within this repo/view.

p4gf_init_repo.py initializes an empty git repo for this view.

NOP if a view with this name already exists.
'''

import os
import re
import sys


import P4

import p4gf_const
import p4gf_copy_p2g
import p4gf_context   # Intentional mis-sequence avoids pylint Similar lines in 2 files
from   p4gf_create_p4 import connect_p4
import p4gf_group
import p4gf_init
import p4gf_lock
import p4gf_log
import p4gf_rc
import p4gf_util
import p4gf_version
import p4gf_view_dirs

LOG = p4gf_log.for_module()
INIT_REPO_EXISTS  = 0  # repo already exists, but may be updated
INIT_REPO_OK      = 1  # repo created successfully
INIT_REPO_NOVIEW  = 2  # missing required template client
INIT_REPO_BADVIEW = 3  # Git Fusion view malformed


def create_p4_client(p4, view_name, client_name, client_root):
    """Create the p4 client to contain Git meta-data mirror.

    Keyword arguments:
    p4          -- Perforce client API
    view_name   -- client view of repository to clone
    client_name -- client that will be created
    client_root -- path for client workspace

    Returns one of the INIT_REPO_* constants.
    """
    # Ensure the client root directory has been created.
    if not os.path.exists(client_root):
        os.makedirs(client_root)

    # If a client for this view already exists, we're probably done.
    #
    # Make sure the client root is correct.
    if p4gf_util.spec_exists(p4, 'client', client_name):
        LOG.debug("%s client already exists for %s", client_name, view_name)
        p4gf_util.ensure_spec_values(p4, 'client', client_name, {'Root':client_root})
        return INIT_REPO_EXISTS

    # Client does not yet exist. We'll have to configure it manually, using
    # the view's name as a template.
    if not p4gf_util.spec_exists(p4, 'client', view_name):
        LOG.warn("requested client %s does not exist, required for creating a mirror", view_name)
        sys.stderr.write("View {} does not exist\n".format(view_name))
        return INIT_REPO_NOVIEW

    # Seed a new client using the view's view as a template.
    LOG.info("client %s does not exist, creating from view %s", client_name, view_name)

    view = p4gf_util.first_value_for_key(
            p4.run('client', '-o', '-t', view_name, client_name),
            'View')

    if not view_depots_ok(p4, view_name, view):
        # nature of problem already reported
        return INIT_REPO_BADVIEW

    desc = ("Created by Perforce Git Fusion for work in {view}."
            .format(view=view_name))
    p4gf_util.set_spec(p4, 'client', spec_id=client_name,
                     values={'Owner'         : p4gf_const.P4GF_USER,
                             'LineEnd'       : 'unix',
                             'View'          : view,
                             'Root'          : client_root,
                             'Host'          : None,
                             'Description'   : desc})
    LOG.debug("successfully created client %s", client_name)
    return INIT_REPO_OK


def view_depots_ok(p4, view_name, view):
    """Check the view for problem depots (e.g. .git-fusion).

    Keyword arguments:
    p4        -- P4 API
    view_name -- used for reporting errors
    view      -- client to be scanned

    Returns False if client view is illegal, True if okay.
    """
    # build list of depots referenced by view
    view_map = P4.Map(view)
    lhs = view_map.lhs()
    referenced_depots = []
    for line in lhs:
        if line.startswith('-'):
            continue
        depot = depot_from_view_lhs(line)
        if not depot in referenced_depots:
            referenced_depots.append(depot)

    # get list of defined depots, build map by depot name
    depots = {depot['name']:depot for depot in p4.run('depots')}

    # check each referenced depot for problems
    for depot in referenced_depots:
        if depot == p4gf_const.P4GF_DEPOT:
            LOG.warn("requested client '%s' must not map depot '%s'",
                     view_name, p4gf_const.P4GF_DEPOT)
            sys.stderr.write("{} depot cannot be mapped in '{}'\n"
                             .format(p4gf_const.P4GF_DEPOT,view_name))
            return False
        if not depot in depots:
            LOG.warn("requested client '%s' must not map undefined depot '%s'",
                     view_name, depot)
            sys.stderr.write("View '{}' maps undefined depot '{}'\n"
                             .format(view_name, depot))
            return False
        if depots[depot]['type'] == 'spec':
            LOG.warn("requested client '%s' must not map spec depot '%s'",
                     view_name, depot)
            sys.stderr.write("spec depots cannot be mapped in '{}'\n"
                             .format(view_name))
            return False
    return True


def depot_from_view_lhs(lhs):
    """extract depot name from lhs of view line"""
    return re.search('^\"?[+-]?//([^/]+)/.*', lhs).group(1)


def create_p4_client_root(p4root):
    """Create a directory to hold the p4 client workspace root."""
    if not os.path.exists(p4root):
        LOG.debug("creating directory %s for p4 client workspace root", p4root)
        os.makedirs(p4root)


def hook_file_content():
    """Return the text of a script that can call our pre-receive hook."""

    lines = ["#! /usr/bin/env bash",
             "",
             "export PYTHONPATH={bin_dir}:$PYTHONPATH",
             "{bin_dir}/{script_name}",
             ""]

    abs_path = os.path.abspath(__file__)
    bin_dir = os.path.dirname(abs_path)
    script_name = "p4gf_pre_receive_hook.py"

    file_content = '\n'.join(lines).format(bin_dir=bin_dir,
                                           script_name=script_name)
    return file_content


def install_hook(git_dir):
    """Install Git Fusion's pre-receive hook"""

    hook_path = os.path.join(git_dir, "hooks", "pre-receive")
    with open (hook_path, 'w') as f:
        f.write(hook_file_content())
    os.chmod(hook_path, 0o755)    # -rwxr-xr-x


def create_perm_groups(p4, view_name):
    """Create the pull and push permission groups, initially empty."""
    p4gf_group.create_view_perm(p4, view_name, p4gf_group.PERM_PULL)
    p4gf_group.create_view_perm(p4, view_name, p4gf_group.PERM_PUSH)


def ensure_deny_rewind(work_tree):
    """Initialize Git config with receive.denyNonFastForwards set to true.
    This prevents the Git user from rewinding our history and possibly
    leading to conflicting history if someone changes the history in
    Perforce at the same time (e.g. amends change descriptions).
    """
    cwd = os.getcwd()
    try:
        os.chdir(work_tree)
        cmd = ['git', 'config', '--local', '--replace-all', 'receive.denyNonFastForwards', 'true']
        result = p4gf_util.popen_no_throw(cmd)
        if result['Popen'].returncode:
            code = result['Popen'].returncode
            LOG.error("error configuring Git repo, git config returned %d", code)
            sys.stderr.write("error: git config failed with {} for {}\n".format(code, work_tree))
    finally:
        os.chdir(cwd)


def init_repo(p4, view_name):
    """Create view and repo if necessary. Does NOT copy p4 to the repo
    (that's p4gf_copy_p2g's job). Returns one of the INIT_REPO_* constants.
    """

    client_name = p4gf_context.view_to_client_name(view_name)

    p4gf_dir    = p4gf_util.p4_to_p4gf_dir(p4)
    view_dirs = p4gf_view_dirs.from_p4gf_dir(p4gf_dir, view_name)
    result = create_p4_client(p4, view_name, client_name, view_dirs.p4root)
    if result > INIT_REPO_OK:
        return result
    create_perm_groups(p4, view_name)
    p4gf_copy_p2g.create_git_repo(view_dirs.GIT_DIR)
    ensure_deny_rewind(view_dirs.GIT_WORK_TREE)
    install_hook(view_dirs.GIT_DIR)
    create_p4_client_root(view_dirs.p4root)
    p4gf_rc.update_file(view_dirs.rcfile, client_name, view_name)
    LOG.debug("repository creation for %s complete", view_name)
    # return the result of creating the client, to indicate if the client
    # had already been set up or not
    return result


def copy_p2g_with_start(view_name, start, view_lock):
    """Invoked 'p4gf_init_repo.py --start=NNN': copy changes from @NNN to @now."""
    ctx = p4gf_context.create_context(view_name, view_lock)
    LOG.debug("connected to P4, p4gf=%s", ctx.p4gf)

    # Copy any recent changes from Perforce to Git.
    p4gf_copy_p2g.copy_p2g_ctx(ctx, start)


def main():
    """set up repo for a view"""
    parser = p4gf_util.create_arg_parser(
    "Initializes Git Fusion Perforce client and Git repository.")
    parser.add_argument('--start', metavar="",
            help='Changelist number to start repo history, default=1')
    parser.add_argument('view', metavar='view',
            help='name of view to be initialized')
    args = parser.parse_args()
    p4gf_version.log_version()

    view_name = p4gf_util.argv_to_view_name(args.view)

    p4gf_util.reset_git_enviro()

    p4 = connect_p4()
    if not p4:
        return 2

    LOG.debug("connected to P4 at %s", p4.port)
    try:
        with p4gf_lock.view_lock(p4, view_name) as view_lock:
            # ensure we have a sane environment
            p4gf_init.init(p4)
            # now initialize the repository
            print("Initializing {}...".format(view_name))
            r = init_repo(p4, view_name)
            if r > INIT_REPO_OK:
                return r
            print("Initialization complete.")

            if args.start:
                start = args.start.lstrip('@')
                print("Copying changes from {}...".format(start))
                copy_p2g_with_start(view_name, start, view_lock)
                print("Copying completed.")
    except P4.P4Exception as e:
        sys.stderr.write("Error occurred: {}\n".format(e))

    return 0

if __name__ == "__main__":
    p4gf_log.run_with_exception_logger(main, write_to_stderr=True)
