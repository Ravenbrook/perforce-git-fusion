#! /usr/bin/env python3.2

"""
MsgId code so that you can seek messages by code rather than US English string.
"""

# Do not import p4gf_version here. This file must be accessible from
# Python 2.6 for OVA web UI.

# pylint:disable=C0103
# Invalid name
# Case is significant and matching P4 C API code here is more important than pep8.


# ErrorSeverity (p4/support/error.h)
#               See also Python P4.P4.E_xxx
E_EMPTY     =  0 # nothing yet
E_INFO      =  1 # something good happened
E_WARN      =  2 # something not good happened
E_FAILED    =  3 # user did something wrong
E_FATAL     =  4 # system broken -- nothing can continue

# ErrorSubsystem (p4/support/errornum.h)
ES_OS       =  0 # OS error
ES_SUPP     =  1 # Misc support
ES_LBR      =  2 # librarian
ES_RPC      =  3 # messaging
ES_DB       =  4 # database
ES_DBSUPP   =  5 # database support
ES_DM       =  6 # data manager
ES_SERVER   =  7 # top level of server
ES_CLIENT   =  8 # top level of client
ES_INFO     =  9 # pseudo subsystem for information messages
ES_HELP     = 10 # pseudo subsystem for help messages
ES_SPEC     = 11 # pseudo subsystem for spec/comment messages
ES_FTPD     = 12 # P4FTP server
ES_BROKER   = 13 # Perforce Broker
ES_P4QT     = 14 # P4V and other Qt based clients

# ErrorGeneric (p4/support/errornum.h)
#               See also Python P4.P4.EV_xxx
EV_NONE     =    0 # misc

                   # The fault of the user
EV_USAGE    = 0x01 # request not consistent with dox
EV_UNKNOWN  = 0x02 # using unknown entity
EV_CONTEXT  = 0x03 # using entity in wrong context
EV_ILLEGAL  = 0x04 # trying to do something you can't
EV_NOTYET   = 0x05 # something must be corrected first
EV_PROTECT  = 0x06 # protections prevented operation

                   # No fault at all
EV_EMPTY    = 0x11 # action returned empty results
EV_FAULT    = 0x21 # inexplicable program fault
EV_CLIENT   = 0x22 # client side program errors
EV_ADMIN    = 0x23 # server administrative action required
EV_CONFIG   = 0x24 # client configuration inadequate
EV_UPGRADE  = 0x25 # client or server too old to interact
EV_COMM     = 0x26 # communications error
EV_TOOBIG   = 0x27 # not ever Perforce can handle this much

def _x_to_text(val, prefix):
    """
    Convert a number to a string of its symbolic constant.

    6 ==> 'EV_PROTECT'

    Return None if number does not match one of our symbolic constants.
    """
    module = globals()
    # Python 2.6: lacks dict comprehension, and 2.6's dict() constructor
    # isn't quite what we want. Unroll the comprehension.
    # known = { module[n]:n for n in module.keys() if n.startswith(prefix) }
    known = {}
    for n in list(module.keys()):
        if n.startswith(prefix):
            known[module[n]] = n
    if val in known:
        return known[val]
    return None


def severity_to_text(sev):
    """
    Convert a Perforce P4.Message.generic to a useful string.

    3 ==> 'E_FAILED'

    Return None if number does not match a symbol E_xxx.
    """
    return _x_to_text(sev, 'E_')


#   subsystem_to_text(sub):
#   Not necessary since P4.Message lacks a subsystem attribute.


def generic_to_text(gen):
    """
    Convert a Perforce P4.Message.severity to a useful string.

    6 ==> 'EV_PROTECT'

    Return None if number does not match a symbol EV_xxx.
    """
    return _x_to_text(gen, 'EV_')


def ErrorOf(sub, cod, sev, gen, arg):
    """
    Return a single numeric code that uniquely identifies this message.

    Analogous to P4 C API's error.h/ErrorOf() preprocessor macro.

    This produces a huge 32-bit integer, NOT what P4.P4Message.msgid returns
    """
    return ((sev<<28)|(arg<<24)|(gen<<16)|(sub<<10)|cod)


def MsgId(sub, cod):
    """
    Return a 16-bit integer that uniquely identifies the important parts of
    a Perforce message ID.

    This is the same as the lower 16 bits of ErrorOf()'s return value.
    """
    return (sub<<10)|cod


# pylint:disable=C0301
# line too long? Too bad. Keep tabular code tabular.

# When copying codes from msgxx.h to here, only the first 2 ErrorId columns
# matter: subsystem and code.
#                                           sub    cod  sev      gen      arg
# ErrorId MsgDm::ProtectsEmpty = { ErrorOf( ES_DM, 456, E_FAILED, EV_ADMIN, 0 ), "xxx" } ;
#
MsgDm_ProtectsEmpty         = MsgId( ES_DM, 456 ) # 6600

MsgDm_LockSuccess           = MsgId( ES_DM, 276 ) # 6420 "%depotFile% - locking" } ;
MsgDm_LockAlready           = MsgId( ES_DM, 277 ) # 6421 "%depotFile% - already locked" } ;
MsgDm_LockAlreadyOther      = MsgId( ES_DM, 278 ) # 6422 "%depotFile% - already locked by %user%@%client%" } ;
MsgDm_LockNoPermission      = MsgId( ES_DM, 279 ) # 6423 "%depotFile% - no permission to lock file" } ;
MsgDm_LockBadUnicode        = MsgId( ES_DM, 525 ) #      "%depotFile% - cannot submit unicode type file using non-unicode server" } ;
MsgDm_LockUtf16NotSupp      = MsgId( ES_DM, 526 ) #      "%depotFile% - utf16 files can not be submitted by pre-2007.2 clients" } ;
MsgDm_UnLockSuccess         = MsgId( ES_DM, 280 ) # 6424 "%depotFile% - unlocking" } ;
MsgDm_UnLockAlready         = MsgId( ES_DM, 281 ) # 6425 "%depotFile% - already unlocked" } ;
MsgDm_UnLockAlreadyOther    = MsgId( ES_DM, 282 ) # 6426 "%depotFile% - locked by %user%@%client%" } ;
MsgDm_OpenUpToDate          = MsgId( ES_DM, 293 ) # E_INFO  "%depotFile%%workRev% - currently opened for %action%" } ;
MsgDm_OpenIsLocked          = MsgId( ES_DM, 298 ) # 6442 "%depotFile% - locked by %user%@%client%" } ;

MsgDm_OpenXOpened           = MsgId( ES_DM, 286 ) #         "%depotFile% - can't %action% exclusive file already opened" } ;
MsgDm_IntegXOpened          = MsgId( ES_DM, 252 ) #         "%depotFile% - can't %action% exclusive file already opened" } ;

MsgDm_OpenWarnOpenStream    = MsgId( ES_DM, 553 ) #         "%depotFile% - warning: cannot submit from non-stream client" } ;

MsgDm_IntegMovedUnmapped    = MsgId( ES_DM, 551 ) #         "%depotFile% - not in client view (remapped from %movedFrom%)" };
MsgDm_ExVIEW                = MsgId( ES_DM, 367 ) # E_WARN  "[%argc% - file(s)|File(s)] not in client view." } ;
MsgDm_ExVIEW2               = MsgId( ES_DM, 477 ) # E_WARN  "%!%[%argc% - file(s)|File(s)] not in client view." } ;
MsgDm_ExFILE                = MsgId( ES_DM, 375 ) # E_WARN  "[%argc% - no|No] such file(s)." } ;
MsgDm_ExPROTECT             = MsgId( ES_DM, 369 ) # 6513 "[%argc% - no|No] permission for operation on file(s)." } ;
MsgDm_ExPROTECT2            = MsgId( ES_DM, 480 ) # 6624 "%!%[%argc% - no|No] permission for operation on file(s)." } ;
