#! /usr/bin/env python3.2
'''Create the user and client that Git Fusion uses when communicating with Perforce.

Does NOT set up any git repos yet: see p4gf_init_repo.py for that.

Eventually there will be more options and error reporting. For now:
* current environment's P4PORT + P4USER is used to connect to Perforce.
* current P4USER must have enough privileges to create users, create clients.

Do not require super privileges for current P4USER or
git-fusion-user. Some customers reject that requirement.

'''


import os
import sys
import time

import P4
import p4gf_const
from p4gf_create_p4 import connect_p4
import p4gf_group
import p4gf_log
import p4gf_p4msg
import p4gf_p4msgid
import p4gf_util
import p4gf_version

LOG = p4gf_log.for_module()
OLD_OBJECT_CLIENT = "git-fusion--p4"
OLDER_OBJECT_CLIENT = "git-fusion-p4"


def _write_user_map(p4, client_name, rootdir):
    """Writes the template user map file to the Git Fusion workspace and
    submits to the repository, if such a file does not already exist.
    """
    mappath = rootdir + '/users/p4gf_usermap'
    if os.path.exists(mappath):
        return
    old_client = p4.client
    try:
        p4.client = client_name
        p4.run('sync', '-q', mappath)
        if not os.path.exists(mappath):
            userdir = os.path.dirname(mappath)
            if not os.path.exists(userdir):
                os.makedirs(userdir)
            with open(mappath, 'w') as mf:
                mf.write("# Git Fusion user map\n")
                mf.write('# Format: Perforce-user [whitespace] Email-addr '
                    '[whitespace] "Full-name"\n')
                mf.write('#joe joe@example.com "Joe User"\n')
            p4.run('add', mappath)
            p4.run('submit', '-d', 'Creating initial p4gf_usermap file via p4gf_init.py')
    except P4.P4Exception as e:
        LOG.warn('error setting up p4gf_usermap file: {}'.format(str(e)))
    finally:
        p4.client = old_client


def _create_client(p4, client_name, p4gf_dir):
    """Create the host-specific Perforce client to enable working with
    the object cache in the .git-fusion depot.
    """
    view = ['//{depot}/... //{client}/...'.format(depot=p4gf_const.P4GF_DEPOT,
                                                  client=client_name)]
    spec_created = False
    if not p4gf_util.spec_exists(p4, "client", client_name):
        # See if the old object clients exist, in which case we will remove them.
        if p4gf_util.spec_exists(p4, "client", OLD_OBJECT_CLIENT):
            p4.run('client', '-df', OLD_OBJECT_CLIENT)
        if p4gf_util.spec_exists(p4, "client", OLDER_OBJECT_CLIENT):
            p4.run('client', '-df', OLDER_OBJECT_CLIENT)
        spec_created = p4gf_util.ensure_spec(
                            p4, "client", spec_id=client_name,
                            values={'Host': None, 'Root': p4gf_dir,
                                    'Description': 'Created by Perforce Git Fusion',
                                    'View': view})
    if not spec_created:
        p4gf_util.ensure_spec_values(p4, "client", client_name,
                                   {'Root': p4gf_dir, 'View': view})


def _maybe_perform_init(p4, started_counter, complete_counter, func):
    """Check if initialization is required, and if so, kick off the
    initialization process. This is done by checking that both the
    started and completed counters are non-zero, in which case the
    initialization is not performed because it was (presumably) done
    already. If both counters are zero, initialization is performed.
    Otherwise, some amount of waiting and eventual lock stealing takes
    place such that initialization is ultimately completed.

    Arguments:
      p4 -- P4 API object
      started_counter -- name of init started counter
      complete_counter -- name of init completed counter
      func -- initialization function to be called, takes a P4 argument.
              Must be idempotent since it is possible initialization may
              be performed more than once.

    Returns True if initialization performed, False if already completed.
    """
    check_times = 0
    inited = False
    while True:
        r = p4.run('counter', '-u', started_counter)
        if r[0]['value'] == "0":
            # Initialization has not been started, try to do so now.
            r = p4.run('counter', '-u', '-i', started_counter)
            if r[0]['value'] == "1":
                # We got the lock, let's proceed with initialization.
                func(p4)
                # Set a counter so we will not repeat initialization later.
                p4.run('counter', '-u', '-i', complete_counter)
                inited = True
                break
        else:
            # Ensure that initialization has been completed.
            r = p4.run('counter', '-u', complete_counter)
            if r[0]['value'] == "0":
                check_times += 1
                if check_times > 5:
                    # Other process failed to finish perhaps.
                    # Steal the "lock" and do the init ourselves.
                    p4.run('counter', '-u', '-d', started_counter)
                    continue
            else:
                # Initialization has occurred already.
                break
        # Give the other process a chance before retrying.
        time.sleep(1)
    return inited


def _global_init(p4):
    """Create global Git Fusion Perforce data:
    * user git-fusion-user
    * depot //.git-fusion
    * group git-fusion-pull
    * group git-fusion-push
    * protects entries
    """

    #
    # The global initialization process below must be idempotent in the sense
    # that it is safe to perform more than once. As such, there are checks to
    # determine if work is needed or not, and if that work results in an
    # error, log and carry on with the rest of the steps, with the assumption
    # that a previous attempt had failed in the middle (or possibly that
    # another instance of Git Fusion has started at nearly the same time as
    # this one).
    #

    with p4gf_group.PermErrorOK(p4):
        p4gf_util.ensure_user_gf(p4)

    with p4gf_group.PermErrorOK(p4):
        p4gf_util.ensure_depot_gf(p4)

    p4gf_group.create_global_perm(p4, p4gf_group.PERM_PULL)
    p4gf_group.create_global_perm(p4, p4gf_group.PERM_PUSH)
    p4gf_group.create_default_perm(p4)

    ### ONCE ADMIN works, downgrade our auto-generated Protections
    ### table to git-fusion-user=admin, not super, and user * = write.

    # Require that single git-fusion-user have admin privileges
    # over the //.git-fusion/ depot
    is_protects_empty = False
    try:
        ### ONCE ADMIN works, remove the use of -u option
        p4.run('protects', '-u', p4gf_const.P4GF_USER, '-m',
               '//{depot}/...'.format(depot=p4gf_const.P4GF_DEPOT))
    except P4.P4Exception:
        if p4gf_p4msg.find_msgid(p4, p4gf_p4msgid.MsgDm_ProtectsEmpty):
            is_protects_empty = True
        # All other errors are fatal, propagated.

    if is_protects_empty:
        ### ONCE ADMIN works, modify the protects table as follows
        # - order the lines in increasing permission
        # - end with at least one user (even a not-yet-created user) with super
        #     write user * * //...
        #     admin user git-fusion-user * //...
        #     super user super * //...
        p4gf_util.set_spec(p4, 'protect', values={
            'Protections': ["super user * * //...",
                            "super user {user} * //...".format(user=p4gf_const.P4GF_USER),
                            "admin user {user} * //{depot}/..."
                            .format(user=p4gf_const.P4GF_USER, depot=p4gf_const.P4GF_DEPOT)]})


def init(p4):
    """Ensure both global and host-specific initialization are completed.
    """
    started_counter = p4gf_const.P4GF_COUNTER_INIT_STARTED
    complete_counter = p4gf_const.P4GF_COUNTER_INIT_COMPLETE
    _maybe_perform_init(p4, started_counter, complete_counter, _global_init)
    client_name = p4gf_util.get_object_client_name()
    started_counter = client_name + '-init-started'
    complete_counter = client_name + '-init-complete'
    home = os.environ.get("HOME")
    p4gf_dir = os.path.join(home, p4gf_const.P4GF_DIR)

    def client_init(p4):
        '''Perform host-specific initialization (and create sample usermap).'''
        # Set up the host-specific client.
        _create_client(p4, client_name, p4gf_dir)
        # Ensure the default user map file is in place.
        _write_user_map(p4, client_name, p4gf_dir)
    if not _maybe_perform_init(p4, started_counter, complete_counter, client_init):
        # If client already created, make sure it hasn't been tweaked.
        ###: do we really need to handle this case? this is here just to pass the tests
        view = ['//{depot}/... //{client}/...'.format(depot=p4gf_const.P4GF_DEPOT,
                client=client_name)]
        p4gf_util.ensure_spec_values(p4, "client", client_name,
                {'Root': p4gf_dir, 'View': view})


def main():
    """create Perforce user and client for Git Fusion"""
    p4gf_version.print_and_exit_if_argv()
    p4gf_version.log_version()
    try:
        p4gf_version.git_version_check()
    # pylint: disable=W0703
    # Catching too general exception
    except Exception as e:
        sys.stderr.write(e.args[0] + '\n')
        exit(1)

    p4 = connect_p4()
    if not p4:
        return 2

    LOG.debug("connected to P4 at %s", p4.port)
    p4gf_util.reset_git_enviro()

    init(p4)

    return 0

if __name__ == "__main__":
    p4gf_log.run_with_exception_logger(main, write_to_stderr=True)
