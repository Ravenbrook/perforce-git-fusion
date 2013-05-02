#! /usr/bin/env python3.2
"""Deletes Git Fusion repositories and Perforce artifacts.

During testing, we often create and destroy Git Fusion repositories.
As such, we need an easy way to clean up and try again, without
destroying the entire Perforce server and starting from scratch. In
particular, this script will:

* delete client git-fusion-<space> workspace files
* delete client git-fusion-<space>

If the --all option is given, all git-fusion-<view> clients are
found and deleted, in addition to the following:

* delete object client workspace files
* obliterate //.git-fusion/objects/...

Invoke with -h for usage information.

"""

import os
import shutil
import sys


import P4
import p4gf_const
import p4gf_context
from p4gf_create_p4 import connect_p4
import p4gf_log
import p4gf_lock
import p4gf_util
import p4gf_view_dirs

LOG = p4gf_log.for_module()


# pylint: disable=C0103
# C0103 Invalid name
# These names are imposed by P4Python
class FilterViewFstatHandler(P4.OutputHandler):
    """OutputHandler for p4 fstat, builds list of files belonging to a view,
    separating those that belong only to this view and those that belong to
    multiple views.
    """
    def __init__(self, view_name):
        P4.OutputHandler.__init__(self)
        self.view_name = view_name
        # List of file names to be removed.
        self.files_to_delete = []
        # List of tuples of files to be modified with updated 'views' attribute.
        self.files_to_modify = []

    def outputStat(self, h):
        """If the file has a 'views' attribute that contains the query
        string, add it to the list.
        """
        if "attr-views" in h:
            if self.view_name == h["attr-views"]:
                self.files_to_delete.append(h["depotFile"])
            else:
                # Strip out the selected view name and save the result so the
                # clean-up code can use it to update the attribute.
                parts = h["attr-views"].split('#')
                parts = [p for p in parts if p != self.view_name]
                new_views = '#'.join(parts)
                # Check that a change was actually made.
                if new_views != h["attr-views"]:
                    self.files_to_modify.append((h["depotFile"], new_views))
        return P4.OutputHandler.HANDLED
# pylint: enable=C0103


def raise_if_homedir(homedir, view_name, rm_list):
    """If any path in rm_list is user's home directory, fail with error
    rather than delete the home directory."""
    for e in rm_list:
        if e == homedir:
            raise P4.P4Exception(("One of view {}'s directories is"
                                  + " user's home directory!").format(view_name))


def print_verbose(args, msg):
    """If args.verbose, print msg, else NOP."""
    if args.verbose:
        print(msg)


def remove_file_or_dir(args, view_name, e):
    """Delete a file or directory."""
    if not os.path.exists(e):
        return
    if os.path.isdir(e):
        print_verbose(args, "Deleting repo {view}'s directory {e}...".format(view=view_name, e=e))
        shutil.rmtree(e)
    elif os.path.isfile(e):
        print_verbose(args, "Deleting repo {view}'s file {e}...".format(view=view_name, e=e))
        os.remove(e)


# pylint: disable=R0912
# Too many branches: fix would result in many arguments to separate functions
def delete_client(args, p4, client_name):
    """Delete the named Perforce client and its workspace. Raises
    P4Exception if the client is not present, or the client configuration is
    not set up as expected.

    Keyword arguments:
    args        -- parsed command line arguments
    p4          -- Git user's Perforce client
    client_name -- name of client to be deleted

    """
    group_list = [p4gf_const.P4GF_GROUP_VIEW_PULL, p4gf_const.P4GF_GROUP_VIEW_PUSH]
    p4.user = p4gf_const.P4GF_USER

    print_verbose(args, "Checking for client {}...".format(client_name))
    if not p4gf_util.spec_exists(p4, 'client', client_name):
        raise P4.P4Exception('No such client "{}" defined'
                             .format(client_name))

    view_name = client_name[len(p4gf_const.P4GF_CLIENT_PREFIX):]
    view_lock = None  # We're clobbering and deleting. Overrule locks.
    try:
        ctx = p4gf_context.create_context(view_name, view_lock)
    except RuntimeError:
        # not a conforming Git Fusion client, ignore it
        return
    command_path = ctx.client_view_path()

    p4gf_dir = p4gf_util.p4_to_p4gf_dir(p4)
    view_dirs = p4gf_view_dirs.from_p4gf_dir(p4gf_dir, view_name)
    rm_list = [view_dirs.view_container]
    homedir = os.path.expanduser('~')
    raise_if_homedir(homedir, view_name, rm_list)

    # Scan for objects associated only with this view so we can either remove
    # them completely or update their 'views' attribute appropriately.
    p4.handler = FilterViewFstatHandler(view_name)
    p4.run("fstat", "-Oa", "-T", "depotFile, attr-views", "//.git-fusion/objects/...")
    objects_to_delete = p4.handler.files_to_delete
    objects_to_modify = p4.handler.files_to_modify
    p4.handler = None

    if not args.delete:
        print("p4 sync -f {}#none".format(command_path))
        print("p4 client -f -d {}".format(client_name))
        for d in rm_list:
            print("rm -rf {}".format(d))
        for to_delete in objects_to_delete:
            print("p4 obliterate -y {}".format(to_delete))
        if objects_to_modify:
            for (fname, views) in objects_to_modify:
                print("attribute -p -n views -v {} {}".format(views, fname))
        for group_template in group_list:
            group = group_template.format(view=view_name)
            print("p4 group -a -d {}".format(group))
        print('p4 counter -u -d {}'.format(p4gf_lock.view_lock_name(view_name)))

    else:
        print_verbose(args, "Removing client files for {}...".format(client_name))
        ctx.p4.run('sync', '-fq', command_path + '#none')
        print_verbose(args, "Deleting client {}...".format(client_name))
        p4.run('client', '-df', client_name)
        for d in rm_list:
            remove_file_or_dir(args, view_name, d)
        bite_size = 1000
        while len(objects_to_delete):
            to_delete = objects_to_delete[:bite_size]
            objects_to_delete = objects_to_delete[bite_size:]
            p4.run("obliterate", "-y", to_delete)
        if objects_to_modify:
            for (fname, views) in objects_to_modify:
                p4.run("edit", fname)
                p4.run("attribute", "-p", "-n", "views", "-v", views, fname)
            p4.run("submit", "-d", "'Removing {} from views attribute'".format(view_name))
        for group_template in group_list:
            delete_group(args, p4, group_template.format(view=view_name))
        _delete_counter(p4, p4gf_lock.view_lock_name(view_name))
# pylint: enable=R0912


def _delete_counter(p4, name):
    """Attempt to delete counter. Report and continue on error."""
    try:
        p4.run('counter', '-u', '-d', name)
    except P4.P4Exception as e:
        if str(e).find("No such counter") < 0:
            LOG.info('failed to delete counter {ctr}: {e}'.
                     format(ctr=name, e=str(e)))


def get_p4gf_localroot(p4):
    """Calculate the local root for the object client."""
    if p4.client != p4gf_util.get_object_client_name():
        raise RuntimeError('incorrect p4 client')
    client = p4.fetch_client()
    rootdir = client["Root"]
    if rootdir.endswith("/"):
        rootdir = rootdir[:len(rootdir) - 1]
    client_map = P4.Map(client["View"])
    lhs = client_map.lhs()
    if len(lhs) > 1:
        # not a conforming Git Fusion client, ignore it
        return None
    rpath = client_map.translate(lhs[0])
    localpath = p4gf_context.client_path_to_local(rpath, p4.client, rootdir)
    localroot = p4gf_context.strip_wild(localpath)
    return localroot


def delete_group(args, p4, group_name):
    """Delete one group, if it exists and it's ours."""
    LOG.debug("delete_group() {}".format(group_name))
    r = p4.fetch_group(group_name)
    if r and r.get('Owners') and p4gf_const.P4GF_USER in r.get('Owners'):
        print_verbose(args, "Deleting group {}...".format(group_name))
        p4.run('group', '-a', '-d', group_name)
    else:
        print_verbose(args, "Not deleting group {group}: Does not exist or {user} is not an owner."
                            .format(group=group_name, user=p4gf_const.P4GF_USER))


def delete_clients(args, p4, client_name):
    """Delete all of the Git Fusion clients, except the object cache
    clients that belong to other hosts.
    """
    r = p4.run('clients', '-e', p4gf_const.P4GF_CLIENT_PREFIX + '*')
    if not r:
        print("No Git Fusion clients found.")
        return
    for spec in r:
        # Skip all object cache clients, not just the one for this host.
        if spec['client'].startswith(p4gf_const.P4GF_OBJECT_CLIENT_PREFIX):
            if spec['client'] != client_name:
                print("Warning: ignoring client {}".format(spec['client']))
        else:
            try:
                delete_client(args, p4, spec['client'])
            except P4.P4Exception as e:
                sys.stderr.write(str(e) + '\n')
                sys.exit(1)


def delete_all(args, p4):
    """Find all git-fusion-* clients and remove them, as well as
    the entire object cache (//.git-fusion/objects/...).

    Keyword arguments:
    args -- parsed command line arguments
    p4   -- Git user's Perforce client

    """
    p4.user = p4gf_const.P4GF_USER
    group_list = [p4gf_const.P4GF_GROUP_PULL, p4gf_const.P4GF_GROUP_PUSH]
    print("Connected to {}".format(p4.port))
    print_verbose(args, "Scanning for Git Fusion clients...")
    client_name = p4gf_util.get_object_client_name()
    delete_clients(args, p4, client_name)
    # Retrieve host-specific initialization counters.
    counters = []
    r = p4.run('counters', '-u', '-e', 'git-fusion*-init-started')
    for spec in r:
        counters.append(spec['counter'])
    r = p4.run('counters', '-u', '-e', 'git-fusion*-init-complete')
    for spec in r:
        counters.append(spec['counter'])
    localroot = get_p4gf_localroot(p4)
    if not args.delete:
        if localroot:
            print("p4 sync -f {}...#none".format(localroot))
            print("p4 client -f -d {}".format(client_name))
            print("rm -rf {}".format(localroot))
        print("p4 obliterate -y //.git-fusion/objects/...")
        for counter in counters:
            print("p4 counter -u -d {}".format(counter))
        for group in group_list:
            print("p4 group -a -d {}".format(group))
    else:
        if localroot:
            print_verbose(args, "Removing client files for {}...".format(client_name))
            p4.run('sync', '-fq', localroot + '...#none')
            print_verbose(args, "Deleting client {}...".format(client_name))
            p4.run('client', '-df', client_name)
            print_verbose(args, "Deleting client {}'s workspace...".format(client_name))
            shutil.rmtree(localroot)
        print_verbose(args, "Obliterating object cache...")
        p4.run('obliterate', '-y', '//.git-fusion/objects/...')
        print_verbose(args, "Removing initialization counters...")
        for counter in counters:
            _delete_counter(p4, counter)
        for group in group_list:
            delete_group(args, p4, group)


def main():
    """Process command line arguments and call functions to do the real
    work of cleaning up the Git mirror and Perforce workspaces.
    """
    # Set up argument parsing.
    parser = p4gf_util.create_arg_parser(
        "Deletes Git Fusion repositories and workspaces.")
    parser.add_argument("-a", "--all", action="store_true",
                        help="remove all known Git mirrors")
    parser.add_argument("-y", "--delete", action="store_true",
                        help="perform the deletion")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print details of deletion process")
    parser.add_argument('views', metavar='view', nargs='*',
                        help='name of view to be deleted')
    args = parser.parse_args()

    # Check that either --all or 'views' was specified.
    if not args.all and len(args.views) == 0:
        sys.stderr.write('Missing view names; try adding the --all option.\n')
        sys.exit(2)

    p4 = connect_p4(client=p4gf_util.get_object_client_name())
    if not p4:
        return 2
    # Sanity check the connection (e.g. user logged in?) before proceeding.
    try:
        p4.fetch_client()
    except P4.P4Exception as e:
        sys.stderr.write("P4 exception occurred: {}".format(e))
        sys.exit(1)

    if args.all:
        try:
            delete_all(args, p4)
        except P4.P4Exception as e:
            sys.stderr.write("{}\n".format(e))
            sys.exit(1)
    else:
        # Delete the client(s) for the named view(s).
        for view in args.views:
            client_name = p4gf_context.view_to_client_name(view)
            try:
                delete_client(args, p4, client_name)
            except P4.P4Exception as e:
                sys.stderr.write("{}\n".format(e))
    if not args.delete:
        print("This was report mode. Use -y to make changes.")

if __name__ == "__main__":
    p4gf_log.run_with_exception_logger(main, write_to_stderr=True)
