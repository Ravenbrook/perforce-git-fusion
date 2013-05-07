#! /usr/bin/env python3.2

"""Script that manages the user map file in Git Fusion. The user map
consists of Perforce user names mapped to the email addresses that appear in
the Git commit logs. This is used to associated Git authors with Perforce
users, for purposes of attribution. The Perforce user accounts are typically
mapped automatically by searching for an account with the same email address
as the Git author. In cases where the email addresses are not the same, the
Perforce administrator may add a mapping to the p4gf_usermap file."""

import os
import re
import sys

import p4gf_const
from p4gf_create_p4 import connect_p4
import p4gf_init
import p4gf_log
import p4gf_p4user
import p4gf_util

try:
    from ravenbrook_data import users as local_users
except ImportError:
    local_users = {}

def read_user_map(p4):
    """Reads the user map file from Perforce into a list of tuples,
    consisting of username, email address, and full name. If no
    such file exists, an empty list is returned.

    Returns a list of 3-tuples: (p4user, email, fullname)
    """
    usermap = []
    client = p4.fetch_client(p4gf_util.get_object_client_name())
    mappath = client['Root'] + '/users/p4gf_usermap'
    # don't let a writable usermap file get in our way
    p4.run('sync', '-fq', mappath)
    if os.path.exists(mappath):
        regex = re.compile('([^ \t]+)[ \t]+([^ \t]+)[ \t]+"([^"]+)"')
        with open(mappath) as mf:
            for line in mf:
                if line:
                    line = line.strip()
                    if line and line[0] != '#':
                        m = regex.search(line)
                        if m:
                            usermap.append((m.group(1), m.group(2), m.group(3)))
    return usermap


def get_p4_users(p4):
    """Retrieve the set of users registered in the Perforce server, in a
    list of tuples consisting of username, email address, and full name. If
    no users exist, an empty list is returned.

    Returns a list of 3-tuples: (p4user, email, fullname)
    """
    users = [(key, value['Email'], value['FullName'])
             for key, value in local_users.items() if 'Email' in value]
    results = p4.run('users')
    if results:
        for r in results:
            users.append((r['User'], r['Email'], r['FullName']))
    return users


def find_by_tuple_index(index, find_value, users):
    """
    Return the first matching element of tuple_list that matches
    find_value.

    Return None if not found.
    """
    for usr in users:
        if usr[index] == find_value:
            return usr
    return None


def find_by_email(addr, users):
    """Retrieve details for user by their email address. Returns the user
    tuple, or None if the user could not be found.
    """
    return find_by_tuple_index(1, addr, users)


def find_by_p4user(p4user, users):
    """Retrieve details for user by their Perforce user account. Returns the user
    tuple, or None if the user could not be found.
    """
    return find_by_tuple_index(0, p4user, users)

# Because tuple indexing is less work for Zig than converting to NamedTuple
TUPLE_INDEX_P4USER   = 0
TUPLE_INDEX_EMAIL    = 1
TUPLE_INDEX_FULLNAME = 2

# pylint: disable=C0103
# C0103 Invalid name
# The correct type is P4User, not p4user.

def tuple_to_P4User(um_3tuple):
    """
    Convert one of our 3-tuples to a P4User.
    """
    p4user = p4gf_p4user.P4User()
    p4user.name      = um_3tuple[TUPLE_INDEX_P4USER  ]
    p4user.email     = um_3tuple[TUPLE_INDEX_EMAIL   ]
    p4user.full_name = um_3tuple[TUPLE_INDEX_FULLNAME]
    return p4user

class UserMap:
    """Mapping of Git authors to Perforce users. Caches the lists of users
    to improve performance when performing repeated searches (e.g. when
    processing a Git push consisting of many commits).
    """

    def __init__(self, p4):
        # List of 3-tuples: first whatever's loaded from p4gf_usermap,
        # then followed by single tuples fetched from 'p4 users' to
        # satisfy later lookup_by_xxx() requests.
        self.users = None

        # List of 3-tuples, filled in only if needed.
        # Complete list of all Perforce user specs, as 3-tuples.
        self.p4users = None

        self.p4 = p4

    def _lookup_by_tuple_index(self, index, value):
        """Return 3-tuple for user whose tuple matches requested value.

        Searches in order:
        * p4gf_usermap (stored in first portion of self.users)
        * previous lookup results (stored in last portion of self.users)
        * 'p4 users' (stored in self.p4users)

        Lazy-fetches p4gf_usermap and 'p4 users' as needed.

        O(n) list scan.
        """
        if not self.users:
            self.users = read_user_map(self.p4)
        # Look for user in existing map. If found return. We're done.
        user = find_by_tuple_index(index, value, self.users)
        if user:
            return user

        # Look for user in Perforce.
        if not self.p4users:
            self.p4users = get_p4_users(self.p4)
        user = find_by_tuple_index(index, value, self.p4users)

        if not user:
            # Look for the "unknown git" user, if any.
            user = find_by_tuple_index(TUPLE_INDEX_P4USER,
                                       p4gf_const.P4GF_UNKNOWN_USER,
                                       self.p4users)

        # Remember this search hit for later so that we don't have to
        # re-scan our p4users list again.
        if user:
            self.users.append(user)

        return user

    def lookup_by_email(self, addr):
        """Retrieve details for user by their email address, returning a
        tuple consisting of the user name, email address, and full name.
        First searches the p4gf_usermap file in the .git-fusion workspace,
        then searches the Perforce users. If no match can be found, and a
        Perforce user named 'unknown_git' is present, then a fabricated
        "user" will be returned. Otherwise None is returned.
        """
        return self._lookup_by_tuple_index(TUPLE_INDEX_EMAIL, addr)

    def lookup_by_p4user(self, p4user):
        """Return 3-tuple for given Perforce user."""
        return self._lookup_by_tuple_index(TUPLE_INDEX_P4USER, p4user)

    def p4user_exists(self, p4user):
        '''Return True if we saw this p4user in 'p4 users' list.'''
        # Look for user in Perforce.
        if not self.p4users:
            self.p4users = get_p4_users(self.p4)
        user = find_by_tuple_index(TUPLE_INDEX_P4USER, p4user, self.p4users)
        if user:
            return True
        return False


def main():
    """Parses the command line arguments and performs a search for the
    given email address in the user map.
    """
    # Set up argument parsing.
    parser = p4gf_util.create_arg_parser(
        "searches for an email address in the user map")
    parser.add_argument('email', metavar='E',
                        help='email address to find')
    args = parser.parse_args()

    # make sure the world is sane
    ec = p4gf_init.main()
    if ec:
        print("p4gf_usermap initialization failed")
        sys.exit(ec)

    p4 = connect_p4(client=p4gf_util.get_object_client_name())
    if not p4:
        sys.exit(1)

    usermap = UserMap(p4)
    user = usermap.lookup_by_email(args.email)
    if user:
        print("Found user {} <{}>".format(user[0], user[2]))
        sys.exit(0)
    else:
        sys.stderr.write("No such user found.\n")
        sys.exit(1)


if __name__ == '__main__':
    p4gf_log.run_with_exception_logger(main, write_to_stderr=True)
