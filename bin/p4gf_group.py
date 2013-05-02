#! /usr/bin/env python3.2

"""Create, modify, and query Perforce groups for user membership."""

import logging

import p4gf_const
import p4gf_log
import p4gf_p4msg
import p4gf_util
import p4gf_version

import P4

# Keys for Perforce spec 'group'
KEY_OWNERS    = 'Owners'
KEY_SUBGROUPS = 'Subgroups'
KEY_USERS     = 'Users'
KEY_GROUP     = 'Group'

PERM_PULL = 'pull'
PERM_PUSH = 'push'

PERM_TO_GROUP       = {PERM_PULL : p4gf_const.P4GF_GROUP_PULL,
                       PERM_PUSH : p4gf_const.P4GF_GROUP_PUSH }

PERM_TO_GROUP_VIEW  = {PERM_PULL : p4gf_const.P4GF_GROUP_VIEW_PULL,
                       PERM_PUSH : p4gf_const.P4GF_GROUP_VIEW_PUSH }

DEFAULT_PERM        = PERM_PUSH

SPEC_TYPE_GROUP     = 'group'

LOG = p4gf_log.for_module()

class PermErrorOK:
    """Squelch EV_PROTECT errors. Propagate all others."""

    def __init__(self, p4):
        self._p4 = p4

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, _traceback):
        """EV_PROTECT message? Squelch. Otherwise let it propagate."""

        # Someone called sys.exit(x). Retain the exit code.
        squelch = (    isinstance(exc_value, P4.P4Exception)
                   and p4gf_p4msg.contains_protect_error(self._p4))
        if squelch:
            LOG.warn(exc_value)
        return squelch


### Promote to p4gf_spec_writer.py
class SpecWriter:
    """An object that knows when a value has changed and thus needs to be saved."""

    def __init__(self, p4, spec_type, name=None):
        self._p4          = p4
        self._spec_type   = spec_type
        self._spec        = None
        self._needs_write = False
        if name:
            self.fetch(name)

    def _fetch_spec(self, spec_type, spec_id):
        """Read one spec and return it."""
        return p4gf_util.first_dict(self._p4.run(spec_type, "-o", spec_id))

    def fetch(self, name):
        """Read from Perforce. usually getting a default or empty spec,
        sometimes picking up some values or changes forced by a
        customer-installed server trigger, sometimes finding an existing
        spec chock full of values that the customer would rather keep.
        """
        self._spec = self._fetch_spec(self._spec_type, name)
        self._needs_write = False
        return self._spec

    def needs_write(self):
        """Have we changed anything that needs writing?"""
        return self._needs_write

    def write_if(self):
        """Write, only if necessary."""
        if self._needs_write:
            return self.write()
        return None

    def write(self):
        """Write, unconditionally. You probably want to call write_if()."""
        p4 = self._p4

        LOG.debug("SpecWriter.write({})".format(self._spec_type))
        p4.input = self._spec
        return p4.run(self._spec_type, "-i")

    def force_list_element(self, key, element):
        """Make sure that <key> exists as a list and contains <element>."""
        if not key in self._spec:
            self._spec[key] = [element]
            self._needs_write = True
            return True

        if not element in self._spec[key]:
            self._spec[key].append(element)
            self._needs_write = True
            return True

        # Already had it, nothing changed
        return False

    ### Add force_value(key, value) later if you actually need it.

class GroupWriter(SpecWriter):
    """A SpecWriter that understands 'p4 group' and its -a/-A rules.

    Rather than running the rather expensive 'p4 groups' command just to
    detect whether or not we need to create this group, we instead look at
    the value for field 'Owners': if our caller changes it via
    force_list_element(), then our caller is most likely creating this
    group from scratch, and needs to be an Owner. Good enough for our
    needs, and avoids a call to 'p4 groups'.
    """

    def __init__(self, p4, name=None):
        """After override."""
        SpecWriter.__init__(self, p4, SPEC_TYPE_GROUP, name)
        self._owner_changed = False
        self._other_changed = False
        
        if LOG.isEnabledFor(logging.DEBUG):
            r = p4.run('groups')
            n = {g['group']:1 for g in r if g['group'].startswith('git-fusion-')}
            LOG.debug('GroupWriter.__init__() current git-fusion-* groups are...\n{}'
                      .format('\n'.join(sorted(n.keys()))))

    def force_list_element(self, key, element):
        """After override to note if we changed Owners."""
        value = SpecWriter.force_list_element(self, key, element)
        if value:
            if key == KEY_OWNERS:
                self._owner_changed = True
            else:
                self._other_changed = True
        return value

    def create_if(self):
        """call create() if necessary."""
        if self.needs_create():
            return self.create()
        return None

    def needs_create(self):
        """Are we the first to write to this group?"""
        return self._owner_changed

    def create(self):
        """Create the group with ourself as the owner.

        Don't bother setting all the fields: Owner is all we need for
        future 'p4 group -a' modification requests to succeed.
        """
        spec_id = self._spec[KEY_GROUP]
        spec = self._fetch_spec(SPEC_TYPE_GROUP, spec_id)
        spec[KEY_OWNERS] = self._spec[KEY_OWNERS]

        self._p4.input = self._spec
        if p4gf_version.p4d_version_supports_admin_user(self._p4):
            return self._p4.run(SPEC_TYPE_GROUP, "-i", "-A")
        else:
            return self._p4.run(SPEC_TYPE_GROUP, "-i")

    def write(self):
        """Create before writing. Pass -a to modify existing group.

        Complete override.
        """
        r = self.create_if()
        # +++ don't need to write if the only thing that changed was Owner.
        if not self._other_changed:
            return r

        #LOG.debug("GroupWriter.write() spec=\n{}".format(self._spec))
        self._p4.input = self._spec
        if p4gf_version.p4d_version_supports_admin_user(self._p4):
            r = self._p4.run(self._spec_type, "-i", "-a")
        else:
            r = self._p4.run(self._spec_type, "-i")
        return r

def create_global_perm(p4, perm):
    """Create git-fusion-pull or git-fusion-push."""
    group_name = PERM_TO_GROUP[perm]
    spec = GroupWriter(p4, group_name)
    spec.force_list_element(KEY_OWNERS, p4gf_const.P4GF_USER)
    spec.write_if()


def create_view_perm(p4, view_name, perm):
    """Create git-fusion-<view>-pull or -push."""
    group_name = PERM_TO_GROUP_VIEW[perm].format(view=view_name)
    subgroup_name = PERM_TO_GROUP[perm]
    spec = GroupWriter(p4, group_name)
    spec.force_list_element(KEY_OWNERS, p4gf_const.P4GF_USER)
    spec.force_list_element(KEY_SUBGROUPS, subgroup_name)
    spec.write_if()


def create_default_perm(p4, perm=DEFAULT_PERM):
    """Create the 'stick all users into this pull/push permission group'
    default counter. If counter already exists with non-zero value,
    leave it unchanged.
    """
    counter = p4gf_util.first_dict_with_key(
                p4.run('counter', '-u', p4gf_const.P4GF_COUNTER_PERMISSION_GROUP_DEFAULT),
                'value')
    if counter != None and counter != 0:
        # Somebody already set it.
        return

    p4.run('counter', '-u',
           p4gf_const.P4GF_COUNTER_PERMISSION_GROUP_DEFAULT,
           perm)


def _can_push(pull, push):
    """If a group grants pull but not push, then nope, no push for you,
    even if some other group grants push.

    If the group grants nothing, return None.
    """
    if pull and not push:
        return False
    if push:
        return True
    return None

def _to_char(x):
    """Convert True/False/None to 1/0/' ' for shorter printing."""
    return { True  : '1',
             False : '0',
             None  : ' ' }[x]

class ViewPerm:
    """Struct containing a single user's permissions on a view.

    Keeper of the actual pull/push authorization logic: if user is a
    member of pull/push group X, then user has pull/pull perm. Honors
    default counter value, too.

    for_user_and_view() is the factory.
    can_pull() and can_push() query for permission.
    write_if() writes user to appropriate view group if necessary.
    """
    def __init__(self):
        self.p4user_name  = None
        self.view_name    = None
        self.view_pull    = None
        self.view_push    = None
        self.global_pull  = None
        self.global_push  = None
        self.default_pull = None
        self.default_push = None

    def __str__(self):
        s = ( "user={user} view={view}"
             + " view:{vpull}{vpush}"
             + " global:{gpull}{gpush}"
             + " default:{dpull}{dpush}").format(
             user = self.p4user_name,
             view = self.view_name,
             vpull = _to_char(self.view_pull),
             vpush = _to_char(self.view_push),
             gpull = _to_char(self.global_pull),
             gpush = _to_char(self.global_push),
             dpull = _to_char(self.default_pull),
             dpush = _to_char(self.default_push))
        return s

    @classmethod
    def for_user_and_view(cls, p4, p4user, view_name):
        """Factory to fetch user's permissions on a view."""
        LOG.debug("for_user_and_view() {u} {v}".format(u=p4user, v=view_name))

        group_list = p4.run('groups', '-i', p4user)
        group_dict = {group['group']:group for group in group_list}
        LOG.debug("group_dict.keys()={}".format(group_dict.keys()))

        vp = ViewPerm()
        vp.p4user_name = p4user
        vp.view_name   = view_name

        vp.view_pull   = p4gf_const.P4GF_GROUP_VIEW_PULL.format(view=view_name) in group_dict
        vp.view_push   = p4gf_const.P4GF_GROUP_VIEW_PUSH.format(view=view_name) in group_dict
        vp.global_pull = p4gf_const.P4GF_GROUP_PULL                             in group_dict
        vp.global_push = p4gf_const.P4GF_GROUP_PUSH                             in group_dict

        value = p4gf_util.first_value_for_key(
                    p4.run('counter', '-u', p4gf_const.P4GF_COUNTER_PERMISSION_GROUP_DEFAULT),
                    'value')
        if value == '0':
            value = DEFAULT_PERM
        vp.default_pull = value == PERM_PULL
        vp.default_push = value == PERM_PUSH
        LOG.debug("counter={}".format(value))

        LOG.debug(vp)
        return vp

    def can(self, perm):
        """Where perm is either 'pull' or 'push', call can_pull() or can_push()."""
        fn = {PERM_PULL : self.can_pull,
              PERM_PUSH : self.can_push}[perm]
        return fn()

    def can_pull(self):
        """If any group grants pull or push permission, or we grant either
        permission by default, then yes, you may pull.
        """
        return (   self.view_pull
                or self.view_push
                or self.global_pull
                or self.global_push
                or self.default_pull
                or self.default_push)

    def can_push(self):
        """If any group grants push permission, or we grant push
        permission by default, then yes, you may push.

        If a group grants pull but not push, then nope, no push for you,
        even if some other group grants push.
        """
        if None != _can_push(self.view_pull,    self.view_push):
            return _can_push(self.view_pull,    self.view_push)

        if None != _can_push(self.global_pull,  self.global_push):
            return _can_push(self.global_pull,  self.global_push)

        if None != _can_push(self.default_pull, self.default_push):
            return _can_push(self.default_pull, self.default_push)

        return None

    def write_if(self, p4):
        """If this user's permission come only from the default counter and
        not from group membership, write this user to the appropriate group
        for this view."""
        if self.needs_write():
            self._write(p4)

    def perm(self):
        """Return best of PERM_PUSH, PERM_PULL, or None."""
        if self.can_push():
            return PERM_PUSH
        if self.can_pull():
            return PERM_PULL
        return None

    def _write(self, p4):
        """Unconditionally add this user to the git-fusion-<view>-pull or
        git-fusion-<view>-push.
        """
        _perm = self.perm()

        subgroup_name   = PERM_TO_GROUP     [_perm]
        group_name      = PERM_TO_GROUP_VIEW[_perm].format(view=self.view_name)

        spec = GroupWriter(p4, group_name)
        spec.force_list_element(KEY_OWNERS,    p4gf_const.P4GF_USER)
        spec.force_list_element(KEY_SUBGROUPS, subgroup_name)
        spec.force_list_element(KEY_USERS,     self.p4user_name)
        spec.write_if()

    def needs_write(self):
        """If we have no permissions granted by group membership,
        but at least one permission granted by default, then that default
        could be written to the group by adding this user to that group.
        """
        return (    not self.view_pull
                and not self.view_push
                and not self.global_pull
                and not self.global_push
                and (   self.default_pull
                     or self.default_push))
