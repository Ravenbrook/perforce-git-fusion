#! /usr/bin/env python3.2

"""Utilities for dealing with P4.Message and p4.messages list."""

# Do not import p4gf_version here. This file must be accessible from
# Python 2.6 for OVA web UI.

import p4gf_p4msgid

def msg_repr(msg):
    """P4.Message.__repr__() strips out msgid. This does not.
    
    Return a string like this:
        gen=EV_ADMIN/35 sev=E_FAILED/3 msgid=6600 Protections table is empty.
    """
    
    return ("gen={gen_t}/{gen} sev={sev_t}/{sev} msgid={msgid} {str}"
            .format( gen_t = p4gf_p4msgid.generic_to_text (msg.generic)
                   , gen   =                             msg.generic
                   , sev_t = p4gf_p4msgid.severity_to_text(msg.severity)
                   , sev   =                             msg.severity
                   , msgid =                             msg.msgid
                   , str   =                         str(msg)
                   ))

def find_msgid(p4, msgid):
    """Return all p4.messages that match the requested id."""
    return [m for m in p4.messages if m.msgid == msgid]

def find_all_msgid(p4, msgids):
    """Return all p4.messages that match the requested id."""
    return [m for m in p4.messages if m.msgid in msgids]


def contains_protect_error(p4):
    """Does this P4 object contain a "You don't have permission..." error?

    P4Exception does not include the error severity/generic/msgid, have to
    dig through P4.messages not P4.errors for numeric codes instead of US
    English message strings.
    """
    for m in p4.messages:
        if (    p4gf_p4msgid.E_FAILED   <= m.severity
            and p4gf_p4msgid.EV_PROTECT == m.generic):
            return True
    return False
