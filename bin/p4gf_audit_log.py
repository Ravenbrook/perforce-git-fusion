#! /usr/bin/env python3.2

"""Record events to the Git Fusion audit log.

Intended primarily for recording incoming requests that come over ssh.
"""
import os
import sys
import syslog

_syslog_ident = "git-fusion-auth"
_syslog_facility = syslog.LOG_USER
_syslog_argv_priority = syslog.LOG_WARNING


def record_error(line):
    """
    Write a line of text to audit log, at priority level 'error'.
    """
    record_line(syslog.LOG_ERR, line)


def record_line(priority, line):
    """Write a line of text to audit log."""
    syslog.openlog(_syslog_ident, syslog.LOG_PID)
    pri = priority | _syslog_facility
    #sys.stderr.write("pri={} msg={}\n".format(pri, line))
    syslog.syslog(pri, line)


def record_argv():
    """Write entire argv to audit log."""
    line = " ".join(sys.argv)
    ssh_env = ["{}={}".format(k, v) for k, v in os.environ.items() if k.startswith('SSH_')]
    if ssh_env:
        line = line + " " + " ".join(ssh_env)
    record_line(_syslog_argv_priority, line)

    # os.environ we see on a real server via real ssh:
    # LANG                : en_US
    # SHELL               : /bin/bash
    # P4PORT: localhost   :1666
    # SSH_ORIGINAL_COMMAND: git-upload-pack 'Talkhouse'
    # SHLVL               : 1
    # PWD                 : /home/git
    # SSH_CLIENT          : 10.0.102.134 49429 22
    # P4USER              : git-fusion-user
    # LOGNAME             : git
    # USER                : git
    # PATH                : /usr/local/git-fusion/bin:/home/git/bin:...
    # :/usr/bin:/sbin:/bin:/usr/games:/opt/vmware/bin
    # MAIL                : /var/mail/git
    # SSH_CONNECTION      : 10.0.102.134 49429 10.0.102.252 22
    # HOME                : /home/git
    # _                   : /usr/local/git-fusion/bin/p4gf_auth_server.py
