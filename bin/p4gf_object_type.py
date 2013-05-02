#! /usr/bin/env python3.2
'''Return the type and extra info of an object stored in the
.git-fusion/objects/... hierarchy.

Checks only the local filesystem for .git-fusion/...
'''

import re

import p4gf_const
import p4gf_log

LOG = p4gf_log.for_module()

TREE    = "tree"
COMMIT  = "commit"
BLOB    = "blob"


class ObjectType:
    '''
    A single sha1 maps to a single type: commit, tree, or blob.

    If commit, maps to 1 or more (changlist, view_name) tuples.

    p4changelists must be a list of 2-tuples:
        [ (change num, view_name), ... ]
    '''
    def __init__(self, sha1, otype, p4changelists=None):
        self.sha1 = sha1
        self.type = otype
        self.p4changelists = p4changelists

    def __str__(self):
        return "{} {} {}".format(self.sha1, self.type, self.p4changelists)
    
    def __repr__(self):
        return str(self)

    def applies_to_view(self, view_name):
        '''
        If we're a BLOB or TREE object, we apply to all view names. Yes.
        If we're a COMMIT object, we only apply to view names referenced in our p4changelists tuple list.
        '''
        if COMMIT != self.type:
            return True
        match = self.view_name_to_changelist(view_name)
        return None != match


    def view_name_to_changelist(self, view_name):
        '''
        Return the matching Perforce changelist number associated with the given view_name.

        Only works for commit objects.

        Return None if no match.
        '''
        match = [cl for (cl, vn) in self.p4changelists if vn == view_name]
        if 0 == len(match):
            return None
        return match[0]

def _sha1_to_star_path(sha1):
    '''Convert '123456' to ('12', '34', '56')'''
    if len(sha1) < 4:
        raise RuntimeError("sha1 must be 4 chars or longer: {0}".format(sha1))
    return (sha1[0:2], sha1[2:4], sha1[4:])


def _deslash(s):
    '''Convert 95/5a/034e3ca8039972b15510465f96f62e629e70
    to 955a034e3ca8039972b15510465f96f62e629e70
    '''
    return s.replace('/', '')

def _filepath_to_object_type(sha1, filepath):
    '''
    Take a file path and parse off the "xxx-commit-nnn" suffix.

    Return an ObjectType instance.
    '''

    re_gfo = re.compile("/objects/([^-]+)-(.*)")
    m = re_gfo.search(filepath)
    if not m:
        raise RuntimeError("could not parse path for sha1: {sha1}\n{filepath}".
                           format(sha1=sha1, filepath=filepath))

    sha1 = _deslash(m.group(1))
    info_list = m.group(2).split('-')

    if 1 == len(info_list):
        if info_list[0] == COMMIT:
            raise RuntimeError("Bug: commit object with no associated p4changelist")
        return ObjectType(sha1, info_list[0], None)

    if  3 <= len(info_list):
        return ObjectType(sha1, info_list[0], [(info_list[1], '-'.join(info_list[2:]))])

    raise RuntimeError("could not parse path for sha1: {sha1}\n{filepath}".
                       format(sha1=sha1, filepath=filepath))


def _sha1_to_object_type_list_p4(sha1, p4):
    '''
    Call Perforce and ask if it has one or more objects for this sha1.

    Return a list of zero or more ObjectType instances that all match this sha1.
    Usually exactly one match for blob and tree and often for commit.
    More than one match for commit if multiple views hold the same
    commit but associated with different Perforce changelists (usually
    due to the same commit pushed to different views).
    '''

    slashed = sha1[0:2] + "/" + sha1[2:4] + "/" + sha1[4:]
    path    = "//{depot}/objects/{slashed}*".format(depot=p4gf_const.P4GF_DEPOT,
                                                    slashed=slashed)
    files   = p4.run("files", path)

    LOG.debug("p4 path={}".format(path))
    LOG.debug("p4 errors={}".format(len(p4.errors)))
    LOG.debug("p4 files={}\n{}".format( len(files)
                                      , '\n'.join([f['depotFile'] for f in files])))
    for e in p4.errors:
        LOG.debug("    {}".format(e))

    return [_filepath_to_object_type(sha1, f['depotFile']) for f in files]


def sha1_to_object_type(sha1, view_name, p4, raise_on_error=True):
    '''
    Look for a file with that sha1's (possibly partial) path and return
    a single matching ObjectType instance.

    If returning a COMMIT object, return only a single COMMIT that matches the given view_name.

    BLOB and TREE objects do not store view_name or need to match. There are never more than one of those per sha1.

    Raises RuntimeError if not found or not unique or not a legal sha1 or
    not long enough.

    Never returns None if raise_on_error==True.
    '''
    try:
        object_type_list = _sha1_to_object_type_list_p4(sha1, p4)
        LOG.debug("sha1_to_object_type {sha1} {view_name} starting with {l}"
                  .format(sha1=sha1, view_name=view_name, l=object_type_list))

        # Strip out any that don't match our view.
        matching_object_type_list = [ot for ot in object_type_list
                                     if ot.applies_to_view(view_name)]
        if 0 == len(matching_object_type_list):
            raise RuntimeError("sha1 not matched: {} for view {}"
                               .format(sha1, view_name))
        if 1 < len(matching_object_type_list):
            raise RuntimeError("sha1 not unique: {} for view {}\n{}"
                               .format(sha1,
                                       view_name,
                                       '\n'.join([str(ot) for ot in matching_object_type_list])))
        return matching_object_type_list[0]
    # pylint: disable=W0702
    # W0702 No exception type(s) specified
    # Yes, I want ALL the exceptions.
    except:
        if raise_on_error:
            raise
    return None
