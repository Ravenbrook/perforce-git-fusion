#! /usr/bin/env python
"""
Utility functions for P4GF that are Python 2.6 compatible, for use by
the OVA management scripts. All other users should import p4gf_util.
"""

import socket
import p4gf_const

Hostname = None


def get_hostname():
    """Return the short name of the machine the Python interpreter is running on.
    """
    global Hostname
    if Hostname is None:
        Hostname = socket.gethostname()
        dot = Hostname.find('.')
        if dot > 0:
            Hostname = Hostname[:dot]
    return Hostname


def get_object_client_name():
    """Produce the name of the host-specific object client for the Git Fusion depot.
    """
    hostname = get_hostname()
    return p4gf_const.P4GF_OBJECT_CLIENT_PREFIX + hostname
