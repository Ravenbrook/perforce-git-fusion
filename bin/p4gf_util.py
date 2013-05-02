#! /usr/bin/env python3.2
"""some utility functions"""

import argparse
import logging
import os
import re
from subprocess import Popen, PIPE

import p4gf_const
import p4gf_log
import p4gf_path
import p4gf_rc
import p4gf_version
import p4gf_view_dirs

# Import the 2.6 compatible pieces, which are shared with OVA scripts.
#
# pylint: disable=W0401
# Wildcard import p4gf_version_26
#
# pylint: disable=W0614
# Unused import %s from wildcard import
from p4gf_util_26 import *

LOG = p4gf_log.for_module()


def create_arg_parser(desc, epilog=None, usage=None):
    """Creates and returns an instance of ArgumentParser configured
    with the options common to all Git Fusion commands. The caller
    may further customize the parser prior to calling parse_args().

    Keyword arguments:
    desc -- the description of the command being invoked

    """
    class VersionAction(argparse.Action):
        """Custom argparse action to display version to stdout (instead
        of stderr, which seems to be the default in argparse)."""
        def __call__(self, parser, namespace, values, option_string=None):
            print(p4gf_version.as_string())
            exit(0)
    # argparse wraps the description and epilog text by default, but
    # could customize using formatter_class
    parser = argparse.ArgumentParser(description=desc, epilog=epilog, usage=usage)
    parser.add_argument("-V", action=VersionAction, nargs=0,
                        help='displays version information and exits')
    # We get -h and --help for free (prints help and exits).
    return parser


def print_dictionary_list(dictlist):
    """Dump a dictlist of dictionaries, for debugging purposes"""
    c = 0
    for adict in dictlist:
        c += 1
        print("\n--%d--" % c)
        for key in adict.keys():
            print("%s: %s" % (key, adict[key]))


def _spec_exists_by_list_scan(p4, spec_info, spec_id):
    """"Table scan for an exact match of id"""
    # Scanning for users when there are NO users? p4d returns ERROR "No such
    # user(s)." instead of an empty result. That's not an error to us, so
    # don't raise it.
    with p4.at_exception_level(p4.RAISE_NONE):
        r = p4.run(spec_info['cmd_list'])
        for spec in r:
            if spec[spec_info['id_list']] == spec_id:
                return True
    return False


def _spec_exists_by_e(p4, spec_info, spec_id):
    """run 'p4 clients -e <name>' to test for existence."""
    r = p4.run(spec_info['cmd_list'], '-e', spec_id)
    for spec in r:
        if spec[spec_info['id_list']] == spec_id:
            return True
    return False


# Instructions on how to operate on a specific spec type.
#
# How do we get a single user? A list of users?
# How do we determine whether a spec already exists?
#
# Fields:
#     cmd_one         p4 command to fetch a single spec: 'p4 client -o'
#                     (the '-o' is implied, not part of this value)
#     cmd_list        p4 command to fetch a list of specs: 'p4 clients'
#     id_one          dict key that holds the spec ID for results of cmd_one: 'Client'
#     id_list         dict key that holds the spec ID for results of cmd_list: 'client'
#     test_exists     function that tells whether a single specific spec
#                     already exists or not
SpecInfo = {
    'client' : { 'cmd_one'     : 'client',
                 'cmd_list'    : 'clients',
                 'id_one'      : 'Client',
                 'id_list'     : 'client',
                 'test_exists' : _spec_exists_by_e },
    'depot'  : { 'cmd_one'     : 'depot',
                 'cmd_list'    : 'depots',
                 'id_one'      : 'Depot',
                 'id_list'     : 'name',
                 'test_exists' : _spec_exists_by_list_scan },
    'protect': { 'cmd_one'     : 'protect',
                 'cmd_list'    : None,
                 'id_one'      : None,
                 'id_list'     : None,
                 'test_exists' : None },
    'user'   : { 'cmd_one'     : 'user',
                 'cmd_list'    : 'users',
                 'id_one'      : 'User',
                 'id_list'     : 'User',
                 'test_exists' : _spec_exists_by_list_scan },
    'group'  : { 'cmd_one'     : 'group',
                 'cmd_list'    : 'groups',
                 'id_one'      : 'Group',
                 'id_list'     : 'group',
                 'test_exists' : _spec_exists_by_list_scan },
}


def spec_exists(p4, spec_type, spec_id):
    """Return True if the requested spec already exists, False if not.

    Raises KeyError if spec type not known to SpecInfo.
    """

    si = SpecInfo[spec_type]
    return si['test_exists'](p4, si, spec_id)


def _to_list(x):
    """Convert a set_spec() args value into something you can += to a list to
    produce a longer list of args.

    A list is fine, pass through unchanged.

    But a string must first be wrapped as a list, otherwise it gets decomposed
    into individual characters, and you really don't want "-f" to turn into
    ['-', 'f']. That totally does not work in 'p4 user -i - f'.

    No support for other types.
    """
    cases = { str : lambda t: [t],
              list: lambda t:  t  }
    return cases[type(x)](x)


def set_spec(p4, spec_type, spec_id=None, values=None, args=None):
    """Create a new spec with the given ID and values.

    spec_id     : string name of fetch+set
    values : vardict of key/values to set
    args   : string or array of additional flags to pass for set.
             Intended for those rare cases when you need -f or -u.

    Raises KeyError if spec_type not known to SpecInfo.
    """
    si = SpecInfo[spec_type]
    _args = ['-o']
    if (spec_id):
        _args.append(spec_id)

    r = p4.run(si['cmd_one'], _args)
    vardict = first_dict(r)
    if (values):
        for key in values:
            if values[key] is None:
                if key in vardict:
                    del vardict[key]
            else:
                vardict[key] = values[key]

    _args = ['-i']
    if (args):
        _args += _to_list(args)
    p4.input = vardict
    try:
        p4.run(si['cmd_one'], _args)
    except:
        LOG.debug("failed cmd: set_spec {type} {id} {dict}"
              .format(type=spec_type, id=spec_id, dict=vardict))
        raise


def ensure_spec(p4, spec_type, spec_id, args=None, values=None):
    """Create spec if it does not already exist, NOP if already exist.

    Return True if created, False if already existed.

    You probably want to check values (see ensure_spec_values) if
    ensure_spec() returns False: the already-existing spec might
    contain values that you do not expect.
    """
    if not spec_exists(p4, spec_type, spec_id):
        LOG.debug("creating %s %s", spec_type, spec_id)
        set_spec(p4, spec_type, spec_id, args=args, values=values)
        return True
    else:
        LOG.debug("%s %s already exists", spec_type, spec_id)
        return False


def ensure_user_gf(p4):
    """Create user git-fusion-user it not already exists.

    Requires that connection p4 has super permissions.

    Return True if created, False if already exists.
    """
    return ensure_spec(p4, "user", spec_id=p4gf_const.P4GF_USER,
                       args='-f',
                       values={'FullName': 'Git Fusion'})


def ensure_group_gf(p4):
    """Create group git-fusion-group it does not already exist.

    Requires that connection p4 has super permissions.

    Return True if created, False if already exists.
    """
    return ensure_spec(p4, "group", spec_id=p4gf_const.P4GF_GROUP,
                       values={'Timeout': 'unlimited', 'Users': [p4gf_const.P4GF_USER]})


def ensure_depot_gf(p4):
    """Create depot .git-fusion if not already exists.

    Requires that connection p4 has super permissions.
    
    Return True if created, False if already exists.
    """
    return ensure_spec(p4, "depot", spec_id=p4gf_const.P4GF_DEPOT,
                       values={'Owner'      : p4gf_const.P4GF_USER,
                               'Description': 'Git Fusion data storage.',
                               'Type'       : 'local',
                               'Map'        : '{depot}/...'
                                              .format(depot=p4gf_const.P4GF_DEPOT)})
    
    
def ensure_spec_values(p4, spec_type, spec_id, values):
    """
    Spec exists but holds unwanted values? Replace those values.

    Does NOT create spec if missing. The idea here is to ensure VALUES,
    not complete spec. If you want to create an entire spec, you
    probably want to specify more values that aren't REQUIRED to match,
    such as Description.
    """
    spec = first_dict(p4.run(spec_type, '-o', spec_id))
    mismatches = {key:values[key] for key in values if spec.get(key) != values[key]}
    LOG.debug("ensure_spec_values(): want={want} got={spec} mismatch={mismatch}"
              .format(spec=spec,
                      want=values,
                      mismatch=mismatches))
    if mismatches:
        set_spec(p4, spec_type, spec_id=spec_id, values=mismatches)
        LOG.debug("successfully updated client %s", spec_id)

def is_legal_view_name(name):
    """
    Ensure that the view name contains only characters which are accepted by
    Perforce for client names. This means excluding the following character
    sequences: @ # * , / " %%x ...
    """
    # According to usage of 'p4 client' we get the following:
    # * Revision chars (@, #) are not allowed
    # * Wildcards (*, %%x, ...) are not allowed
    # * Commas (,) not allowed
    # * Slashes (/) not allowed
    # * Double-quote (") => Wrong number of words for field 'Client'.
    # Additionally, it seems that just % causes problems on some systems,
    # with no explanation as to why, so for now, prohibit them as well.
    if re.search('[@#*,/"]', name) or '%' in name or '...' in name:
        return False
    return True

def argv_to_view_name(argv1):
    """Convert a string passed in from argv to a usable view name.

    Provides a central place where we can switch to unicode if we ever want
    to permit non-ASCII chars in view names.

    Also defends against bogus user input like shell injection attacks:
    "p4gf_init.py 'myview;rm -rf *'"

    Raises an exception if input is not a legal view name.
    """
    # To switch to unicode, do this:
    # argv1 = argv1.decode(sys.getfilesystemencoding())
    if not is_legal_view_name(argv1):
        raise RuntimeError("git-fusion: Not a valid client name: '{view}'".format(view=argv1))
    return argv1


def reset_git_enviro():
    """Clear GIT_DIR and other GIT_xxx  environment variables,
    then chdir to GIT_WORK_TREE.

    This undoes any strangeness that might come in from T4 calling 'git
    --git-dir=xxx --work-tree=yyy' which might cause us to erroneously
    operate on the "client-side" git repo when invoked from T4.

    or from git-receive-pack chdir-ing into the .git dir.
    """
    git_env_key = [k for k in os.environ if k.startswith("GIT_")]
    for key in git_env_key:
        del os.environ[key]

    # Find our view name, use that to calculate and chdir into our GIT_WORK_TREE.
    rc_path = p4gf_path.cwd_to_rc_file()
    if (rc_path):
        view_name = rc_path_to_view_name(rc_path)
        LOG.debug("reset_git_enviro rc_path_to_view_name({rc_path}) returned {view_name}"
                  .format(rc_path=rc_path, view_name=view_name))
        p4gf_dir    = rc_path_to_p4gf_dir(rc_path)
        LOG.debug("reset_git_enviro rc_path_to_p4gf_dir({rc_path}) returned {p4gf_dir}"
                  .format(rc_path=rc_path, p4gf_dir=p4gf_dir))
        view_dirs  = p4gf_view_dirs.from_p4gf_dir(p4gf_dir, view_name)
        os.chdir(view_dirs.GIT_WORK_TREE)


def pretty_dict(d):
    """Return a pretty-printed dict.

    pprint fails sometimes. Dunno why. Don't care.
    """
    keywidths = [len(key) for key in d]
    maxwidth = max(keywidths)
    fmt = "%-" + str(maxwidth) + "s : "

    keys = [key for key in d]
    keys.sort()
    result = ""
    for key in keys:
        result += (fmt % key) + d[key] + "\n"
    return result


def pretty_env():
    """Return a pretty-printed os.environ.

    no, pprint does not work. Dunno why. Don't care.
    """
    return pretty_dict(os.environ)


def _args_to_string(*args):
    """Return a single string representation of a popen() arg list."""
    if (len(args) == 1):
        return str(args[0])
    else:
        return " ".join(args)

def _log_cmd_result(args_as_string, p, fd, expect_error):
    """Record command result in debug log.

    If command completed successfully, record output at DEBUG level so that
    folks can suppress it with cmd:INFO. But if command completed with error
    (non-zero return code), then record its output at ERROR level so that
    cmd:INFO users still see it.
    """
    cmd_logger = logging.getLogger("cmd")

    if (not p.returncode) or expect_error :
        # Things going well? Don't care if not? 
        # Then log only if caller is REALLY interested.
        log_level = logging.DEBUG
    else:
        # Things going unexpectedly poorly? Log almost all of the time.
        log_level = logging.ERROR
        if not cmd_logger.isEnabledFor(logging.DEBUG):
            # We did not log the command. Do so now.
            cmd_logger.log(log_level, args_as_string)

    cmd_logger.getChild("exit").log(log_level, "exit: {0}".format(p.returncode))

    if len(fd[0]):
        cmd_logger.getChild("out").log(log_level, "out :\n{0}".format(fd[0]))

    if len(fd[1]):
        cmd_logger.getChild("err").log(log_level, "err :\n{0}".format(fd[1]))


def popen_no_throw(cmd, stdin=None):
    """Call p4gf_util.popen() and return, even if popen() returns
    a non-zero returncode.

    Prefer p4gf_util.popen() to p4gf_util.popen_no_throw(): popen() will
    automatically fail fast and report errors. popen_no_throw() will
    silently fail continue on, probably making things worse. Use
    popen_no_throw() only when you expect, and recover from, errors.
    """
    return _popen_no_throw_internal(cmd, True, stdin)


def _popen_no_throw_internal(cmd, expect_error, stdin=None):
    '''
    Internal Popen() wrapper that records command and result to log.
    '''
    # By taking a command list vs a string, we implicitly avoid shell quoting.
    if not isinstance(cmd, list):
        LOG.error("_popen_no_throw_internal() cmd not of list type: {}".format(cmd))
        return None
    logging.getLogger("cmd").debug(' '.join(cmd))
    # Note that we are intentionally _not_ using the shell, to avoid vulnerabilities.
    p = Popen(cmd, stdout=PIPE, stderr=PIPE, stdin=PIPE)
    fd = p.communicate(stdin)

    _log_cmd_result(cmd, p, fd, expect_error)

    result = { "cmd"   : ' '.join(cmd),
               "out"   : fd[0].decode(),
               "err"   : fd[1].decode(),
               "Popen" : p }
    return result


def popen(cmd, stdin=None):
    """Wrapper for subprocess.Popen() that logs command and output to debug log.

    Returns three-way dict: (out, err, Popen)
    """

    result = _popen_no_throw_internal(cmd, False, stdin)

    p = result['Popen']
    if not p.returncode:
        return result

    raise RuntimeError(("Command failed: {cmd}"
                        + "\nexit code: {ec}."
                        + "\nstdout:\n{out}"
                        + "\nstderr:\n{err}")
                       .format(ec=p.returncode,
                               cmd=result['cmd'],
                               out=result['out'],
                               err=result['err']))


def sha1_for_branch(branch):
    """Return the sha1 of a branch.
    
    Return None if no such branch.
    """
    d = popen_no_throw(['git', 'show-ref', '--verify', '--hash', branch])
    out = str(d['out'].strip())
    if not out:
        return None
    return out


def git_ref_master():
    """Return the master ref of the current working directory's git repo.

    Return None if repository is empty, or if anything goes wrong.

    """

    # Fully qualify "master" with "refs/heads/master" to avoid other
    # refs line "refs/origin/master" which also appear in an unqualified
    # 'git show-refs master'.
    return sha1_for_branch("refs/heads/master")


def git_head_sha1():
    """Return the sha1 that goes with the current HEAD."""
    
    ### Replace git log with git rev-list.
    d = popen_no_throw(['git', 'log', '-1', '--oneline', '--abbrev=40', 'HEAD', '--'])
    out = d['out']
    if len(out) == 0:
        return None
    sha1 = out.split(' ')[0]
    return sha1


def git_root_commit(sha1):
    """
    Return the root of the commit hierarchy: the ancestor commit who has
    no parent commit.
    
    In linear history this is always a single commit.
    
    In history with one or more merge commits, it is possible to have
    one or more roots:
    
    A--B--Cm-D   Both A and X are "roots" of Cm and D.
         /
    X--Y-
    """
    d = popen_no_throw(['git', 'rev-list', '--max-parents=0', sha1])
    return d['out'].splitlines()


def start_of_history(sha1):
    """
    Return the very first commit in a history that ends at sha1.
    """
    l = git_root_commit(sha1)
    if len(l) != 1:
        raise RuntimeError("Not exactly one root commit for {}.".format(sha1))
    return l[0]


def git_checkout(sha1):
    """Switch to the given sha1."""
    popen(['git', 'checkout', sha1])


def checkout_detached_master():
    """Dereference master and switch to that sha1."""

    sha1 = git_ref_master()
    if (sha1):
        git_checkout(sha1)


class HeadRestorer:
    """An RAII class that restores the current working directory's HEAD and
    working tree to the sha1 it had when created.

    with p4gf_util.HeadRestorer() :
        ... your code that can raise exceptions...
    """

    def __init__(self):
        """
        Remember the current HEAD sha1.
        """
        self.__sha1__ = git_head_sha1()
        if not self.__sha1__:
            logging.getLogger("HeadRestorer").debug(
                "get_head_sha1() returned None, will not restore")

    def __enter__(self):
        """nop"""
        return None

    def __exit__(self, _exc_type, _exc_value, _traceback):
        """Restore then propagate"""
        ref = self.__sha1__
        if ref:
            popen(['git', 'reset', '--hard', ref])
            popen(['git', 'checkout', ref])
        return False  # False == do not squelch any current exception


def cwd_to_view_name():
    """Glean the view name from the current working directory's path:
    it's the 'foo' in 'foo/.git/'. If the Git Fusion RC file is
    present, then the view name will be read from the file.
    """
    config = p4gf_rc.read_config()
    view_name = p4gf_rc.get_view(config)
    if view_name is None:
        # Fall back to using directory name as view name.
        path = p4gf_path.cwd_to_dot_git()
        (path, _git) = os.path.split(path)
        (path, view_name) = os.path.split(path)
    return view_name


def test_vars():
    """Return a dict of test key/value pairs that the test script controls.

    Used to let test scripts control internal behavior, such as causing
    a loop to block until the test script has a chance to introduce a
    conflict at a known time.

    Eventually this needs to be read from env or a file or something
    that the test script controls.

    Return an empty dict if not testing (the usual case).
    """
    config = p4gf_rc.read_config()
    if not config:
        LOG.debug("test_vars no config.")
        return {}
    if not config.has_section(p4gf_const.P4GF_TEST):
        LOG.debug("test_vars config, no [test].")
        return {}
    d = {i[0]:i[1] for i in config.items(p4gf_const.P4GF_TEST)}
    LOG.debug("test_vars returning {}".format(d))
    return d


def test_var_to_dict(var_val):
    """Cheesy 'key:val' parser that probably could be better replaced
    with ConfigParser.
    """
    r = {}
    lines = var_val.split("\n")
    for line in lines:
        line = line.strip()
        colon = line.find(":")
        key = line[:colon].strip()
        val = line[1+colon:].strip()
        if not key:
            continue
        r[key] = val
    return r

def rc_path_to_view_name(rc_path):
    """Read the rc file at rc_path and return the view name stored
    within the rc file."""
    config = p4gf_rc.read_config(rc_path=rc_path)
    return p4gf_rc.get_view(config)


def rc_path_to_p4gf_dir(rc_path):
    """Return the path to the outer ".git-fusion" container of all
    things Git Fusion.

    This is an alternative to p4_to_p4gf_dir() which avoids a trip to
    Perforce to read an object client root, and will probably become a
    source of bugs later if the admin changes the client root but does
    not move the .git-fusion dir.
    """
    return p4gf_path.find_ancestor(rc_path, p4gf_const.P4GF_DIR)


def p4_to_p4gf_dir(p4):
    """Return the local filesystem directory that serves as
    the root of all things Git Fusion.

    This is the client root of the host-specific object client.

    This is also the direct ancestor of any .git-fusion.rc file,
    and an ancestor of views/<view>/... per-repo/view directories.

    Initialized to ~/.git-fusion in p4gf_init.py, admin is free to
    change this later.
    """
    spec = p4.fetch_client(get_object_client_name())
    return spec['Root']


def dict_to_attr(input_dict, attr_map, dest_object):
    """For each key,value pair in input_dict, copy its value into dest_object
    as an attribute, not a dict value. Use attrmap to convert from input_dict
    key to dest_object attribute name."""
    for k, v in list(input_dict.items()):
        attr_name = attr_map[k]
        setattr(dest_object, attr_name, v)


def view_list(p4):
    '''
    Return a list of all known Git Fusion views.
    Reads them from 'p4 clients'.

    Omits the host-specific 'git-fusion--*' object clients.

    Return empty list if none found.
    '''
    prefix_len = len(p4gf_const.P4GF_CLIENT_PREFIX)
    r = p4.run('clients', '-e', p4gf_const.P4GF_CLIENT_PREFIX + '*')
    l = []
    for spec in r:
        if not spec['client'].startswith(p4gf_const.P4GF_OBJECT_CLIENT_PREFIX):
            l.append(spec['client'][prefix_len:])
    return l


def first_dict(result_list):
    '''
    Return the first dict result in a p4 result list.

    Skips over any message/text elements such as those inserted by p4broker.
    '''
    for e in result_list:
        if isinstance(e, dict):
            return e
    return None


def first_dict_with_key(result_list, key):
    '''
    Return the first dict result that sets the required key.
    '''
    for e in result_list:
        if isinstance(e, dict) and key in e:
            return e
    return None


def first_value_for_key(result_list, key):
    '''
    Return the first value for dict with key.
    '''
    for e in result_list:
        if isinstance(e, dict) and key in e:
            return e[key]
    return None
