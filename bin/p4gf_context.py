#! /usr/bin/env python3.2
"""Config and Context classes"""
import logging
import os
import tempfile
import time

from P4 import Map, P4Exception

from p4gf_create_p4 import create_p4
from p4gf_gitmirror import GitMirror
import p4gf_const
import p4gf_protect
import p4gf_util

LOG = logging.getLogger(__name__)


def client_path_to_local(clientpath, clientname, localrootdir):
    """ return client syntax path converted to local syntax"""
    return localrootdir + clientpath[2 + len(clientname):]


def strip_wild(path):
    """ strip trailing ... from a path

    ... must be present; no check is made
    """
    return path[:len(path) - 3]


def check_client_view_gf(lhs):
    """ check that the client view fits our strict requirements

    The client used for P4GF data must contain exactly one line in its view,
    mapping from //.git-fusion/... to //client/somewhere/...

    Multiple wildcards, special characters, any other wacky stuff forbidden.
    """
    if not len(lhs) == 1 or not lhs[0] == "//{0}/...".format(p4gf_const.P4GF_DEPOT):
        bad_client_view(lhs, "view not equal to: //.git-fusion/...")


def bad_client_view(lhs, why):
    """all purpose exception for invalid client view"""
    raise RuntimeError("# P4GFContext: Invalid client view for primitive " +
                       "git-fusion.\n{why}\n{lhs}".format(lhs=lhs, why=why))


def view_to_client_name(view):
    """prepend view name with git-fusion-"""
    return "git-fusion-{0}".format(view)


def create_context(view_name, view_lock):
    """Return a Context object that contains the connection details for use
    in communicating with the Perforce server."""
    cfg = Config()
    p4 = create_p4()
    cfg.p4port = p4.env('P4PORT')
    cfg.p4user = p4gf_const.P4GF_USER
    cfg.p4client = view_to_client_name(view_name)
    cfg.p4client_gf = p4gf_util.get_object_client_name()
    cfg.view_name = view_name
    ctx = Context(cfg)
    ctx.view_lock = view_lock  # None OK: can run without a lock.
    return ctx


class Config:
    """perforce config"""

    def __init__(self):
        self.p4port = None
        self.p4user = None
        self.p4client = None     # client for view
        self.p4client_gf = None  # client for gf
        self.view_name = None    # git project name


class Context:
    """a single git-fusion view/repo context"""

    def __init__(self, config):
        self.config = config
        self.p4 = self.__make_p4(client=self.config.p4client)
        self.p4gf = self.__make_p4(client=self.config.p4client_gf)
        self.mirror = GitMirror(config.view_name)
        self.timezone = None
        self.get_timezone()
        self._user_to_protect = None
        self.view_dirs = None
        self.view_lock = None
        self.tempdir = tempfile.TemporaryDirectory(prefix=p4gf_const.P4GF_TEMP_DIR_PREFIX)

        # Environment variable set by p4gf_auth_server.py.
        self.authenticated_p4user = os.environ.get(p4gf_const.P4GF_AUTH_P4USER)

        # paths set up by set_up_paths()
        self.gitdepotroot = "//" + p4gf_const.P4GF_DEPOT + "/"
        self.gitlocalroot = None
        self.gitrootdir = None
        self.contentlocalroot = None
        self.contentclientroot = None
        self.clientmap = None
        self.clientmap_gf = None
        self.__set_up_paths()

    def user_to_protect(self, user):
        """Return a p4gf_protect.Protect instance that knows
        the given user's permissions."""
        # Lazy-create the user_to_protect instance since not all
        # Context-using code requires it.
        if not self._user_to_protect:
            self._user_to_protect = p4gf_protect.UserToProtect(self.p4)
        return self._user_to_protect.user_to_protect(user)

    def __make_p4(self, client=None):
        """create a connection to the perforce server"""
        p4 = create_p4(port=self.config.p4port,
                       user=self.config.p4user)
        if client:
            p4.client = client
        else:
            p4.client = self.config.p4client
        try:
            p4.connect()
        except P4Exception as e:
            raise RuntimeError("Failed P4 connect: {}".format(str(e)))
        p4.exception_level = 1
        return p4

    def client_view_path(self):
        """return client path for whole view, including ... wildcard"""
        return self.contentclientroot

    def get_timezone(self):
        """get server's timezone via p4 info"""
        server_date = p4gf_util.first_value_for_key(self.p4.run("info"), 'serverDate')
        self.timezone = server_date.split(" ")[2]

    def __set_up_paths(self):
        """set up depot and local paths for both content and P4GF

        These paths are derived from the client root and client view.
        """
        self.__set_up_content_paths()
        self.__set_up_p4gf_paths()

    def __set_up_content_paths(self):
        """set up depot and local paths for both content and P4GF

        These paths are derived from the client root and client view.
        """

        client = self.p4.fetch_client()
        self.clientmap = Map(client["View"])

        # local syntax client root, force trailing /
        self.contentlocalroot = client["Root"]
        if not self.contentlocalroot.endswith("/"):
            self.contentlocalroot += '/'

        # client sytax client root with wildcard
        self.contentclientroot = '//' + self.p4.client + '/...'

    def __set_up_p4gf_paths(self):
        """set up depot and local paths for P4GF

        These paths are derived from the client root and client view.
        """

        client = self.p4gf.fetch_client()

        # client root, minus any trailing /
        self.gitrootdir = client["Root"]
        if self.gitrootdir.endswith("/"):
            self.gitrootdir = self.gitrootdir[:-1]

        self.clientmap_gf = Map(client["View"])
        lhs = self.clientmap_gf.lhs()
        check_client_view_gf(lhs)

        for lpath in lhs:
            rpath = self.clientmap_gf.translate(lpath)
            self.gitlocalroot = strip_wild(
                   client_path_to_local(rpath, self.p4gf.client, self.gitrootdir))

    def __str__(self):
        return "\n".join(["Git data in Perforce:   " + self.gitdepotroot + "...",
                          "                        " + self.gitlocalroot + "...",
                          "Exported Perforce tree: " + self.contentlocalroot + "...",
                          "                        " + self.contentclientroot,
                          "timezone: " + self.timezone])

    def __repr__(self):
        return str(self) + "\n" + repr(self.mirror)

    def log_context(self):
        """Dump connection info, client info, directories, all to log category
        'context' as INFO."""

        log = logging.getLogger('context')
        if not log.isEnabledFor(logging.INFO):
            return

        # Dump client spec as raw untagged text.
        self.p4.tagged = 0
        client_lines_raw = self.p4.run('client', '-o')[0].splitlines()
        self.p4.tagged = 1
        # Strip comment header
        client_lines = [l for l in client_lines_raw if not l.startswith('#')]

        # Dump p4 info, tagged, since that includes more pairs than untagged.
        p4info = p4gf_util.first_dict(self.p4.run('info'))
        key_len_max = max(len(k) for k in p4info.keys())
        info_template = "%-{}s : %s".format(key_len_max)

        log.info(info_template % ('P4PORT',     self.p4.port))
        log.info(info_template % ('P4USER',     self.p4.user))
        log.info(info_template % ('P4CLIENT',   self.p4.client))
        log.info(info_template % ('p4gfclient', self.p4gf.client))

        for k in sorted(p4info.keys(), key=str.lower):
            log.info(info_template % (k, p4info[k]))

        for line in client_lines:
            log.info(line)

    def heartbeat(self):
        '''
        If we have a view lock, update its heartbeat.

        If our lock is cleared, then raise a RuntimeException
        canceling our current task.
        '''
        if not self.view_lock:
            return

        if self.view_lock.canceled():
            raise RuntimeError("Canceling: lock {} lost."
                               .format(self.view_lock.counter_name()))

        self.view_lock.update_heartbeat()

        # For some of the tests, slow down the operation by sleeping briefly.
        key = p4gf_const.P4GF_TEST_LOCK_VIEW_SLEEP_AFTER_HEARTBEAT_SECONDS
        test_vars = p4gf_util.test_vars()
        if key in test_vars:
            sleep_seconds = test_vars[key]
            LOG.debug("Test: sleeping {} seconds...".format(sleep_seconds))
            time.sleep(float(sleep_seconds))
            LOG.debug("Test: sleeping {} seconds done".format(sleep_seconds))
