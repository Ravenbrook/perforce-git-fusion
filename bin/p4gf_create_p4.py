#! /usr/bin/env python3.2
"""Create a new P4.P4() instance."""

import sys

import P4

import p4gf_const
import p4gf_log
import p4gf_version

LOG = p4gf_log.for_module()


def create_p4(port=None, user=None, client=None):
    """Return a new P4.P4() instance with its prog set to
    'P4GF/2012.1.PREP-TEST_ONLY/415678 (2012/04/14)'

    There should be NO bare calls to P4.P4().

    """
    p4 = P4.P4()
    p4.prog = p4gf_version.as_single_line()

    if port:
        p4.port = port
    if user:
        p4.user = user
    if client:
        p4.client = client

    return p4


def connect_p4(port=None, user=None, client=None):
    """Connects to a P4D instance and checks the version of the server. The
    connected P4.P4 instance is returned. If the version of the server is
    not acceptable, a message is printed and RuntimeError is raised.
    If the client is unable to connect to the server, then None is returned.
    """
    if not user:
        user = p4gf_const.P4GF_USER
    p4 = create_p4(port, user, client)
    try:
        p4.connect()
        LOG.debug("connect_p4(): u={} {}".format(user, p4))
    except P4.P4Exception as e:
        LOG.error('Failed P4 connect: {}'.format(str(e)))
        sys.stderr.write("error: cannot connect, p4d not running?\n")
        return None
    p4gf_version.p4d_version_check(p4)
    return p4
