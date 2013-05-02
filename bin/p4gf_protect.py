#! /usr/bin/env python3.2

"""Wrapper for 'p4 protect' table."""

import P4

from   p4gf_path import enquote, dequote

# privilege kevel constants
LIST = 'list'
READ = 'read'
OPEN = 'open'
WRITE = 'write'
ADMIN = 'admin'
SUPER = 'super'
REVIEW = 'review'
BRANCH = 'branch'

# Granting privilege N implicitly grants privilege N-1 and all those
# before it.
ORDER = [LIST, READ, OPEN, WRITE, ADMIN, SUPER]

# 'review' permission grants 'read' and thus 'list'.
REVIEW_GRANT = [LIST, READ, REVIEW]

# In case you feel like iterating.
KNOWN  = [LIST, READ, OPEN, WRITE, ADMIN, SUPER, REVIEW, BRANCH]

WILDCARDS = ['...', '*'] + ['%%{}'.format(n) for n in range(0, 10)]

def permission_includes(granted, requested):
    """Does the granted permission level include the requested permission?

    Most granted permissions include all those before them.

    See 'p4 help protect'

    Does not work so good for '=branch' permission, which can only be
    denied, and if denied, prohibits "files as a source for 'p4
    integrate'" which is beyond permission_includes()'s ability to
    determine.
    """

    # The usual sequence.
    if (requested in ORDER) and (granted in ORDER):
        return ORDER.index(requested) <= ORDER.index(granted)

    # 'review' grants a subset of rights
    if granted == REVIEW:
        return requested in REVIEW_GRANT

    # Granting specific permissions: '=write' grants 'write',
    # and only 'review' grants 'review'.
    if (   (granted ==       requested)
        or (granted == '=' + requested)):
        return True

    if not requested in KNOWN:
        raise RuntimeError("Unknown permission requested: {}".format(requested))

    return False


# pylint:disable=C0301
# line too long? Too bad. Keep tabular code tabular.

#
# If this is the protects output for a user:
#
#   write user * * //...
#   list user flynn * -//...
#   review user flynn * //depot/a/a2
#
# p4.run('protects', '-u', 'flynn', '//...')
#
# Returns a list like this:
# [
#  {             'line': '1', 'perm': 'write' , 'user': '*',     'host': '*', 'depotFile': '//...'},
#  {'unmap': '', 'line': '3', 'perm': 'list'  , 'user': 'flynn', 'host': '*', 'depotFile': '//...'},
#  {             'line': '4', 'perm': 'review', 'user': 'flynn', 'host': '*', 'depotFile': '//depot/a/a2'}
# ]
#
# Sam says "unmap" revokes ALL permissions for that path.
#

def _create_map_for_perm(protects_dict_list, requested_perm):
    """Return a new MapApi instance that maps in all of
    protects_dict_list depotFile lines that grant the requested_perm
    and excludes all lines that exclude it.
    """

    # Build a list of matching lines.
    lines = []
    for pd in protects_dict_list:
        if 'unmap' in pd: # Unmapping ANY permission unmaps ALL permissions
            lines.append('-' + pd['depotFile'])
            continue
        if permission_includes(pd['perm'], requested_perm):
            lines.append(pd['depotFile'])

    # P4.Map() requires space-riddled paths to be quoted paths
    # to avoid accidentally splitting a # single path into lhs/rhs.
    quoted = [enquote(x) for x in lines]
    mapapi = P4.Map(quoted)
    return mapapi


class Protect:
    """A wrapped MapApi instance that knows how to tell if a sequence of
    'p4 protects' lines grants a requested permission on a depotFile.
    """

    def __init__(self, protects=None):
        # Ordered list of dicts, the result of 'p4 protects ...'.
        if protects is None:
            protects = []
        self._protects_dict_list = protects

        # Lazy-created MapApi instances.
        # Key = requested permission, Val = Map instance.
        self._perm_to_mapapi = {}

    @classmethod
    def from_protects(cls, protects_dict_list):
        """Create and return a new Protect instance seeded with the
        result of p4.run('protects',...)
        """
        return cls(protects_dict_list)

    def map_for_perm(self, requested_perm):
        """Return a MapApi instance that maps in all the paths that
        grant the requested perm, and no paths that lack it or have had
        that perm explicitly revoked through exclusion lines.

        Lazy-create and cache these MapApi instances.
        """
        mapapi = self._perm_to_mapapi.get(requested_perm)
        if not mapapi:
            mapapi = _create_map_for_perm(self._protects_dict_list, requested_perm)
            self._perm_to_mapapi[requested_perm] = mapapi
        return mapapi

def _create_protect_for_user(p4, user):
    """Create a new Protect object from 'p4 protects -u <user>'
    If user is None, return empty Protect"""
    if not user:
        return Protect()

    r = p4.run('protects', '-u', user)
    return Protect.from_protects(r)


class UserToProtect:
    """Caching/Factory object that maintains a cache of Protect objects,
    one per requested user, and knows how to create those Protect objects
    on the fly if you ask for one it does not (yet) have cached.
    """

    def __init__(self, p4):
        self._p4 = p4
        self._user_to_protect = {}

    def user_to_protect(self, user):
        """Return a Protect object for user, from cache if one already
        exists, creating one via 'p4 protects -u <user>' if not.
        """
        p = self._user_to_protect.get(user)
        if not p:
            p = _create_protect_for_user(self._p4, user)
            self._user_to_protect[user] = p
        return p


def _map_inclusion_can_bypass_files(mapapi):
    """Do any of the map's lines contain wildcards other than terminal ... ?
    Any exclusions?

    The optimization code can reliable tell if every file in
    //depot/a/... is within //depot/..., but cannot reliable tell if
    //depot/a/... is within //depot/*x or depot/*/foo/... . Yes it is
    possible to enhance the optimization code to reliable deal with a
    few more mapping permutations, but trailing ... is the biggest
    benefit for the least complexity.
    """
    for line in mapapi.lhs():
        line = dequote(line)

        # Exclusion?
        if line.startswith('-'):
            return False

        # Trailing dot-dot-dot expected, supported.
        # Remove so it does not trigger rejection.
        if line.endswith('...'):
            line = line[:-3]

        # Any other wildcard? Going to have to run 'p4 files'. Sorry.
        for wild in WILDCARDS:
            if wild in line:
                return False
    return True


COMPLETELY_INCLUDED = 'completely_included'
COMPLETELY_EXCLUDED = 'completely_excluded'
UNKNOWN             = 'unknown'

def map_includes_entire_view(mapapi, view_list):
    """Can we tell if the view is completely included in the map?

    +++ This is a big optimization when it works: if we can
    determine COMPLETELY_INCLUED or COMPLETELY_EXCLUDED, there is no
    need to run 'p4 files //<client>/...' to check each file path
    against protects.

    COMPLETELY_INCLUDED : view is completely included, no exclusions or
    omissions make it possible for any path in view to be excluded.

    COMPLETELY_EXCLUDED : view does not overlap mapapi at all. No path
    in view can be included.

    UNKNOWN : exclusions, partial overlap, or wildcards other than
    terminal ... make it impossible to tell from just a view list
    whether all of the views _current_ files are included in the mapapi.
    Must run 'p4 -c <client> files //<client>/...' and pump each returned
    file path through mapapi to test for inclusion.
    """
    view_mapapi = P4.Map(view_list)
    if (   not _map_inclusion_can_bypass_files(mapapi)
        or not _map_inclusion_can_bypass_files(view_mapapi)):
        return UNKNOWN

    included = []
    excluded = []
    for line in view_mapapi.lhs():
        line = dequote(line)
        if mapapi.includes(line):
            included.append(line)
        else:
            excluded.append(line)
    if included and not excluded:
        return COMPLETELY_INCLUDED
    if not included and excluded:
        return COMPLETELY_EXCLUDED
    return UNKNOWN
