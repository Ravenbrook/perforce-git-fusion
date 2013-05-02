#! /usr/bin/env python3.2

"""
Perform Git Fusion initialization that requires "super" permissions.

* Create group git-fusion-group
* Create user  git-fusion-user
* Create depot .git-fusion
* Grant admin permission to git-fusion-user
* Configure dm.protects.allow.admin=1

Must be run with current P4USER set to a super user.
"""
import sys

import p4gf_const
from   p4gf_create_p4 import create_p4
import p4gf_util
import p4gf_p4msg
import p4gf_p4msgid
import p4gf_version


# report() will print all messages at or below this level.
# 0 is just errors
# 1 is short/occasional status
# 2 is NOISY
QUIET =  0 # Still want errors
ERROR =  0
WARN  =  1
INFO  =  2
DEBUG =  3

VERBOSE_MAP =      { "QUIET"    : QUIET
                   , "ERROR"    : ERROR
                   , "ERR"      : ERROR
                   , "WARNING"  : WARN
                   , "WARN"     : WARN
                   , "INFO"     : INFO
                   , "DEBUG"    : DEBUG }
VERBOSE_SEQUENCE = [ "QUIET"
                   , "ERROR"
                   , "ERR"
                   , "WARNING"
                   , "WARN"
                   , "INFO"
                   , "DEBUG" ]

# Default verbose level.
VERBOSE_LEVEL = INFO

P4PORT = None
P4USER = None
p4     = None

KEY_PERM_MAX    = 'permMax'
KEY_PROTECTIONS = 'Protections'
KEY_VALUE       = 'Value'

CONFIGURABLE_ALLOW_ADMIN = 'dm.protects.allow.admin'

def report(lvl, msg):
    """Tell the human what's going on."""
    if lvl <= VERBOSE_LEVEL:
        print(msg)

    #else:
    #    print "# v={v} l={l} m={m}".format(v=VERBOSE_LEVEL, l=lvl, m=msg)


def fetch_protect():
    """Return protect table as a list of protect lines."""

    with p4.at_exception_level(p4.RAISE_NONE):
        r = p4.run('protect','-o')
        report(DEBUG, 'p4 protect:\n{}'.format(r))
    if p4.errors:
        report(ERROR, "Unable to run 'p4 protect -o'.")
        for e in p4.errors:
            report(ERROR, e)
        exit(2)

    protections = p4gf_util.first_value_for_key(r, KEY_PROTECTIONS)
    return protections


def ensure_user():
    """Create Perforce user git-fusion-user if not already exists."""
    created = p4gf_util.ensure_user_gf(p4)
    if created:
        report(INFO, "User {} created.".format(p4gf_const.P4GF_USER))
    else:
        report(INFO, "User {} already exists. Not creating."
                     .format(p4gf_const.P4GF_USER))
    return created


def ensure_group():
    """Create Perforce group git-fusion-group if not already exists."""
    created = p4gf_util.ensure_group_gf(p4)
    if created:
        report(INFO, "Group {} created.".format(p4gf_const.P4GF_GROUP))
    else:
        report(INFO, "Group {} already exists. Not creating."
                     .format(p4gf_const.P4GF_GROUP))
    return created


def ensure_depot():
    """Create depot .git-fusion if not already exists."""
    created = p4gf_util.ensure_depot_gf(p4)
    if created:
        report(INFO, "Depot {} created.".format(p4gf_const.P4GF_DEPOT))
    else:
        report(INFO, "Depot {} already exists. Not creating."
                     .format(p4gf_const.P4GF_DEPOT))
    return created


def ensure_protect(protect_lines):
    """Require that 'p4 protect' table includes grant of admin to git-fusion-user.

    """
    with p4.at_exception_level(p4.RAISE_NONE):
        r = p4.run('protects', '-m', '-u', p4gf_const.P4GF_USER)

    if p4gf_p4msg.find_msgid(p4, p4gf_p4msgid.MsgDm_ProtectsEmpty):
        report(INFO, "Protect table empty. Setting....")

    report(DEBUG, 'p4 protects -mu git-fusion-user\n{}'.format(r))
    perm = p4gf_util.first_value_for_key(r, KEY_PERM_MAX)
    if perm and perm in ['admin', 'super']:
        report(INFO, ( "Protect table already grants 'admin'"
                     + " to user {}. Not changing").format(p4gf_const.P4GF_USER))
        return False

    l = protect_lines
    if p4gf_version.p4d_version_supports_admin_user(p4):
        perm = 'admin'
    else:
        perm = 'super'
    l.append('{perm} user {user} * //...'.format(perm=perm, user=p4gf_const.P4GF_USER))

    p4gf_util.set_spec(p4, 'protect', values={KEY_PROTECTIONS : l})
    report(INFO, "Protect table modified. {} granted admin permission."
                 .format(p4gf_const.P4GF_USER))
    return True


def ensure_protects_configurable():
    """Grant 'p4 protects -u' permission to admin users."""

    if not p4gf_version.p4d_version_supports_admin_user(p4):
        return

    v = p4gf_util.first_value_for_key(
            p4.run('configure', 'show', CONFIGURABLE_ALLOW_ADMIN),
            KEY_VALUE)
    if v == '1':
        report(INFO, 'Configurable {} already set to 1. Not setting.'
                     .format(CONFIGURABLE_ALLOW_ADMIN))
        return False

    p4.run('configure', 'set', '{}=1'.format(CONFIGURABLE_ALLOW_ADMIN))
    report(INFO, 'Configurable {} set to 1.'
                 .format(CONFIGURABLE_ALLOW_ADMIN))
    return True


def main():
    """Do the thing."""
    try:
        p4gf_version.git_version_check()

        # Connect.
        global P4PORT, P4USER, p4
        p4 = create_p4(port=P4PORT, user=P4USER)
        P4PORT = p4.port
        P4USER = p4.user
        report(INFO, "P4PORT : {}".format(p4.port))
        report(INFO, "P4USER : {}".format(p4.user))
        p4.connect()
        p4gf_version.p4d_version_check(p4)

        # Require that we have super permission.
        # Might as well keep the result in case we need to write a new protect
        # table later. Saves a 'p4 protect -o' trip to the server
        protect_lines = fetch_protect()

        ensure_user()
        ensure_group()
        ensure_depot()
        ensure_protect(protect_lines)
        ensure_protects_configurable()

    # pylint: disable=W0703
    # Catching too general exception
    except Exception as e:
        sys.stderr.write(e.args[0] + '\n')
        exit(1)


# pylint:disable=C0301
# line too long? Too bad. Keep tabular code tabular.
def parse_argv():
    """Copy optional port/user args into global P4PORT/P4USER."""

    parser = p4gf_util.create_arg_parser(
    "Creates Git Fusion user, depot, and protect entry.")
    parser.add_argument('--port',    '-p', metavar='P4PORT', nargs=1,                   help='P4PORT of server')
    parser.add_argument('--user',    '-u', metavar='P4USER', nargs=1,                   help='P4USER of user with super permissions.')
    parser.add_argument('--verbose', '-v', metavar='level',  nargs='?', default='INFO', help='Reporting verbosity.')
    parser.add_argument('--quiet',   '-q',                   action='store_true',       help='Report only errors. Same as --verbose QUIET')
    args = parser.parse_args()

    # Handle verbosity first so that we can honor it when processing args.
    if args.quiet:
        args.verbose = QUIET
    elif not args.verbose:
        # -v with no arg means "debug"
        args.verbose = DEBUG
    # Convert text levels like "INFO" to numeric 2
    if (str(args.verbose).upper() in VERBOSE_MAP.keys()):
        args.verbose = VERBOSE_MAP[str(args.verbose).upper()]
    elif not args.verbose in VERBOSE_MAP.values():
        report(ERROR, "Unknown --verbose value '{val}'. Try {good}"
                      .format(val=args.verbose,
                              good=", ".join(VERBOSE_SEQUENCE)))
        exit(2)

    global VERBOSE_LEVEL, P4PORT, P4USER
    VERBOSE_LEVEL = args.verbose
    report(DEBUG, "args={}".format(args))

    # Optional args, None if left unset
    if args.port:
        P4PORT = args.port[0]
    if args.user:
        P4USER = args.user[0]


if __name__ == "__main__":
    parse_argv()
    main()

