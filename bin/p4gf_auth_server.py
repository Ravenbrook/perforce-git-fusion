#! /usr/bin/env python3.2
"""
p4gf_auth_server.py

A shell replacement that ssh invokes to run push or pull commands on the
Git Fusion server.

Arguments:
--user=p4user  required  which Perforce user account is the pusher/puller
--keyfp=<key>  required  SSH key fingerprint of key used to authenticate
<command>      required  one of git-upload-pack or git-receive-pack
                         no other commands permitted

Record the request, along with p4user and key fingerprint and requested
git command, to an audit log.

Run the appropriate protocol interceptor for git-upload-pack or
git-receive-pack.

Reject attempt if p4user lacks read privileges for the entire view.

Reject unknown git command

"""

import argparse
import logging
import os
import re
import subprocess
import shutil
import sys
import traceback

import p4gf_audit_log
import p4gf_const
import p4gf_context
import p4gf_copy_p2g
from   p4gf_create_p4 import connect_p4
import p4gf_group
import p4gf_init
import p4gf_init_repo
import p4gf_lock
import p4gf_log
import p4gf_p4msg
from p4gf_repolist import RepoList
import p4gf_util
import p4gf_version
import p4gf_view_dirs

LOG = p4gf_log.for_module()

def check_protects(p4):
    """Check that the protects table is either empty or that the Git
    Fusion user is granted sufficient privileges. Returns False if this
    is not the case.
    """
    return p4gf_version.p4d_supports_protects(p4)


def illegal_option(option):
    """Trying to sneak a shell command into my world? Please do not do that."""
    if ';' in option:
        return True

    # git-upload-pack only understands --strict and --timeout=<n>.
    # git-receive-pack understands no options at all.
    re_list = [ re.compile("^--strict$"),
                re.compile(r"^--timeout=\d+$")]
    for reg in re_list:
        if reg.match(option):
            return False
    return True


COMMAND_TO_PERM    = {'git-upload-pack'  : p4gf_group.PERM_PULL,
                      'git-receive-pack' : p4gf_group.PERM_PUSH}


def cleanup_client(ctx, view_name):
    """Clean up the failed client and workspace after an error occurs while
    creating the initial clone. If the client does not exist, nothing is done.
    """
    client_name = p4gf_const.P4GF_CLIENT_PREFIX + view_name
    if not p4gf_util.spec_exists(ctx.p4, 'client', client_name):
        return

    LOG.debug('cleaning up failed view {}'.format(view_name))
    command_path = ctx.client_view_path()

    p4gf_dir = p4gf_util.p4_to_p4gf_dir(ctx.p4)
    view_dirs = p4gf_view_dirs.from_p4gf_dir(p4gf_dir, view_name)

    ctx.p4.run('sync', '-fq', command_path + '#none')
    ctx.p4.run('client', '-df', client_name)
    for vdir in [view_dirs.view_container]:
        LOG.debug('removing view directory {}'.format(vdir))
        if os.path.isdir(vdir):
            shutil.rmtree(vdir)
        elif os.path.isfile(vdir):
            os.remove(vdir)


def record_reject(msg, _traceback=None):
    """
    Write line to both auth audit log and stderr. Will append a
    newline when writing to standard error.

    Separate stack traces to a second optional parameter, so that
    we can still dump those to audit log, but NEVER to stderr, which the
    git user sometimes sees.
    """
    p4gf_audit_log.record_error(msg)
    if _traceback:
        p4gf_audit_log.record_error(_traceback)
    sys.stderr.write(msg + '\n')


class CommandError(RuntimeError):
    """
    An error that ExceptionAuditLogger recognizes as one that
    requires a report of exception, but not of stack trace.
    """
    def __init__(self, val, usage=None):
        self.usage = usage # Printed to stdout if set
        RuntimeError.__init__(self, val)


class ExceptionAuditLogger:
    """Write all exceptions to audit log, then propagate."""

    def __init__(self):
        pass

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, _traceback):
        # Skip calls to exit().
        if (exc_type == SystemExit):
            return False

        if (exc_type == CommandError):
            record_reject("{}".format(exc_value))
            if exc_value.usage:
                print(exc_value.usage)
            return False

        if (exc_type):
            str_list = traceback.format_exception(exc_type, exc_value, _traceback)
            s = "".join(str_list)
            record_reject("{}".format(exc_value), s)

        return False # False = do not squelch. Propagate


def is_special_command(view):
    """See if view is actually a special command masquerading as a view"""
    return view in [p4gf_const.P4GF_UNREPO_HELP,
                    p4gf_const.P4GF_UNREPO_INFO,
                    p4gf_const.P4GF_UNREPO_LIST]


def run_special_command(view, p4, user):
    """If view is a special command run it and return True; otherwise return False"""

    # @help: dump contents of help.txt, if file exists
    if p4gf_const.P4GF_UNREPO_HELP == view:
        helppath = os.path.join(os.path.dirname(__file__), 'help.txt')
        if not os.path.exists(helppath):
            sys.stderr.write("no help.txt found\n")
        else:
            with open(helppath, "r") as helpfile:
                for line in helpfile:
                    sys.stderr.write(line)
        return True

    # @info: dump info to stderr
    if p4gf_const.P4GF_UNREPO_INFO == view:
        sys.stderr.write(p4gf_version.as_string())
        sys.stderr.write("Server address: {}\n".format(p4.port))
        return True

    # @list: dump list of repos to stderr
    if p4gf_const.P4GF_UNREPO_LIST == view:
        repos = RepoList.list_for_user(p4, user).repos
        if len(repos):
            width = max([len(r[0]) for r in repos])
            sys.stderr.write("\n".join(["{name:<{width}} {perm}".format(width=width,
                                                                        name=r[0],
                                                                        perm=r[1])
                                        for r in repos]) + "\n")
        else:
            sys.stderr.write("no repositories found\n")
        return True

    return False


def parse_args(argv):
    """Parse the given arguments into a struct and return it.

    On error, print error to stdout and return None.

    If unable to parse, argparse.ArgumentParser.parse_args() will exit,
    so we add our own exit() calls, too. Otherwise some poor unsuspecting
    programmer would see all our "return None" calls and think that
    "None" is the only outcome of a bad argv.
    """

    # pylint:disable=C0301
    # line too long? Too bad. Keep tabular code tabular.
    parser = p4gf_util.create_arg_parser("Records requests to audit log, performs only permitted requests.",
                            usage="usage: p4gf_auth_server.py [-h] [-V] [--user] [--keyfp] git-upload-pack | git-receive-pack [options] <view>")
    parser.add_argument('--user',  metavar="",                           help='Perforce user account requesting this action')
    parser.add_argument('--keyfp', metavar="",                           help='ssh key used to authenticate this connection')
    parser.add_argument('command', metavar="command", nargs=1,           help='git-upload-pack or git-receive-pack, plus options')
    parser.add_argument('options', metavar="", nargs=argparse.REMAINDER, help='options for git-upload-pack or git-receive-pack')

    # reverse git's argument modifications
    # pylint:disable=W1401
    # raw strings don't play well with this lambda function. 
    fix_arg = lambda s: s.replace("'\!'", "!").replace("'\\''", "'")
    argv = [fix_arg(arg) for arg in argv]
    args = parser.parse_args(argv)

    if not args.command[0] in COMMAND_TO_PERM:
        raise CommandError("Unknown command '{bad}', must be one of {good}."
                           .format(bad=args.command[0],
                                   good = ", ".join(COMMAND_TO_PERM.keys())),
                           usage = parser.usage)

    if not args.options:
        raise CommandError("Missing directory in {cmd} <view>"
                           .format(cmd=args.command[0]))

    # Carefully remove quotes from any view name, allowing for imbalanced quotes.
    view_name = args.options[-1]
    if view_name[0] == '"' and view_name[-1] == '"' or\
            view_name[0] == "'" and view_name[-1] == "'":
        view_name = view_name[1:-1]
    # Allow for git+ssh URLs where / separates host and repository.
    if view_name[0] == '/':
        view_name = view_name[1:]
    args.options[-1] = view_name

    # Reject impossible view names/client spec names
    if not is_special_command(view_name) and not p4gf_util.is_legal_view_name(view_name):
        raise CommandError("Illegal view name '{}'".format(view_name))

    for o in args.options[:-1]:
        if illegal_option(o):
            raise CommandError("Illegal option: {}".format(o))

    # Require --user if -V did not early return.
    if not args.user:
        raise CommandError("--user required.",
                           usage=parser.usage)

    return args


def _raise_p4gf_perm():
    '''
    User-visible permission failure.
    '''
    raise CommandError("git-fusion-user not granted sufficient privileges.")


def _check_lock_perm(p4):
    '''
    Permission check: can git-fusion-user set our lock counter? If not, you
    know what to do.
    '''
    with p4gf_group.PermErrorOK(p4):
        with p4gf_lock.CounterLock(p4, "git_fusion_auth_server_lock"):
            pass
    if p4gf_p4msg.contains_protect_error(p4):
        _raise_p4gf_perm()


def _check_authorization(view_perm, user, command, view_name):
    '''
    Does view_perm grant permission to run command? If not, raise an exception.
    '''
    required_perm = COMMAND_TO_PERM[command]
    if view_perm.can(required_perm):
        return
    raise CommandError("User {user} not authorized for {command} on {view}."
                       .format(user=user,
                               command=command,
                               view=view_name))


def _call_original_git(ctx, args):
    '''
    Pass to git-upload-pack/git-receive-pack. But with the view converted to
    an absolute path to the Git Fusion repo.
    '''
    converted_argv = args.options[:-1]
    converted_argv.append(ctx.view_dirs.GIT_DIR)
    cmd_list = args.command + converted_argv
    logging.getLogger("cmd").debug(' '.join(cmd_list))
    # Note that we are intentionally _not_ using the shell, to avoid vulnerabilities.
    code = subprocess.call(cmd_list)
    logging.getLogger("cmd.exit").debug("exit: {0}".format(code))
    return code


def main():
    """set up repo for a view"""
    with ExceptionAuditLogger():
        args = parse_args(sys.argv[1:])
        if not args:
            return 1

        # Record the p4 user in environment. We use environment to pass to
        # git-invoked hook. We don't have to set ctx.authenticated_p4user because
        # Context.__init__() reads it from environment, which we set here.
        os.environ[p4gf_const.P4GF_AUTH_P4USER] = args.user

        # print "args={}".format(args)
        view_name = args.options[-1]

        p4gf_util.reset_git_enviro()
        p4 = connect_p4()
        if not p4:
            return 2
        LOG.debug("connected to P4: %s", p4)

        _check_lock_perm(p4)

        if not check_protects(p4):
            _raise_p4gf_perm()

        if run_special_command(view_name, p4, args.user):
            return 0

        # Go no further, create NOTHING, if user not authorized.
        view_perm = p4gf_group.ViewPerm.for_user_and_view(p4, args.user, view_name)
        _check_authorization(view_perm, args.user, args.command[0], view_name)
        # Create Git Fusion server depot, user, config. NOPs if already created.
        p4gf_init.init(p4)

        with p4gf_lock.view_lock(p4, view_name) as view_lock:

            # Create Git Fusion per-repo client view mapping and config.
            #
            # NOPs if already created.
            # Create the empty directory that will hold the git repo.
            init_repo_status = p4gf_init_repo.init_repo(p4, view_name)
            if init_repo_status == p4gf_init_repo.INIT_REPO_OK:
                repo_created = True
            elif init_repo_status == p4gf_init_repo.INIT_REPO_EXISTS:
                repo_created = False
            else:
                return 1

            # If authorization came from default, not explicit group
            # membership, copy that authorization to a group now. Could
            # not do this until after p4gf_init_repo() has a chance to
            # create not-yet-existing groups.
            view_perm.write_if(p4)

            # Now that we have valid git-fusion-user and
            # git-fusion-<view> client, replace our temporary P4
            # connection with a more permanent Context, shared for the
            # remainder of this process.
            ctx = p4gf_context.create_context(view_name, view_lock)
            del p4
            LOG.debug("reconnected to P4, p4gf=%s", ctx.p4gf)

            # Find directory paths to feed to git.
            ctx.view_dirs = p4gf_view_dirs.from_p4gf_dir(ctx.gitrootdir, view_name)
            ctx.log_context()

            # cd into the work directory. Not all git functions react well
            # to --work-tree=xxxx.
            cwd = os.getcwd()
            os.chdir(ctx.view_dirs.GIT_WORK_TREE)

            # Copy any recent changes from Perforce to Git.
            try:
                p4gf_copy_p2g.copy_p2g_ctx(ctx)
            except:
                # Dump failure to log, BEFORE cleanup, just in case
                # cleanup ALSO fails and throws its own error (which
                # happens if we're out of memory).
                LOG.error(traceback.format_exc())

                if repo_created:
                    # Return to the original working directory to allow the
                    # config code to call os.getcwd() without dying, since
                    # we are about to delete the current working directory.
                    os.chdir(cwd)
                    cleanup_client(ctx, view_name)
                raise

            # Detach git repo's workspace from master before calling
            # original git, otherwise we won't be able to push master.
            p4gf_util.checkout_detached_master()

            # Flush stderr before returning control to Git.
            # Otherwise Git's own output might interrupt ours.
            sys.stderr.flush()

            return _call_original_git(ctx, args)


if __name__ == "__main__":
    # Ensure any errors occurring in the setup are sent to stderr, while the
    # code below directs them to stderr once rather than twice.
    try:
        with p4gf_log.ExceptionLogger(squelch=False, write_to_stderr_=True):
            p4gf_audit_log.record_argv()
            p4gf_version.log_version()
            p4gf_version.git_version_check()
    # pylint: disable=W0702
    except:
        # Cannot continue if above code failed.
        exit(1)
    # main() already writes errors to stderr, so don't let logger do it again
    p4gf_log.run_with_exception_logger(main, write_to_stderr=False)
