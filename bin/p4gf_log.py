#! /usr/bin/env python3.2
"""Utilities for configuring Git Fusion's debug/error/audit log."""

import inspect
import logging
import logging.handlers
import os
import socket
import sys
import syslog
import tempfile
import traceback

import p4gf_const

_config_filename_default    = '/etc/git-fusion.log.conf'
_configured                 = False
_enable_print               = False
_level_choices              = "raise debug info warning error critical"
_level_choice_list          = _level_choices.split()

_figleaf_output_file        = '.figleaf'
_syslog_ident               = 'git-fusion'


def find_config_file():
    """Look for p4gf_log.config in current dir, then in same dir as this .py script.
    Return first match, or None if not found.
    """

    # Check test-imposed config file
    if p4gf_const.P4GF_TEST_LOG_CONFIG_PATH in os.environ:
        path = os.environ[p4gf_const.P4GF_TEST_LOG_CONFIG_PATH]
        if (os.path.exists(path)):
            return path

    # Check ./p4gf_log.config
    if (os.path.exists(_config_filename_default)):
        return _config_filename_default

    return None

class P4GFSysLogFormatter(logging.Formatter):
    '''
    A formatter for SysLogHandler that inserts category and level into the message.
    '''
    def __init__(self, fmt=None, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        '''
        Prepend category and level.
        '''
        #r = ", ".join(["{}:{}".format(k,v) for k,v in record.__dict__.iteritems()]
        return ("{name} {level} {message}"  # {r}"
                .format( name     = record.name
                       , level    = record.levelname
                       , message  = record.message
                       #, r = ", ".join(s)
                       ))

class P4GFSysLogHandler(logging.handlers.SysLogHandler):
    '''
    A SysLogHandler that knows to include an ident string.

    Python 3 has this, but not 2.7.3.
    '''
    def __init__(self,
                 address=('localhost', logging.handlers.SYSLOG_UDP_PORT),
                 facility=syslog.LOG_USER,
                 socktype=socket.SOCK_DGRAM):
        logging.handlers.SysLogHandler.__init__(self, address, facility, socktype)
        self.formatter = P4GFSysLogFormatter()
    
    def emit(self, record):
        msg = self.format(record)
        syspri = self.mapPriority(record.levelname)
        # encodePriority() expects 1 for "user", shifts it to 8. but
        # syslog.LOG_USER is ALREADY shifted to 8, passing it to
        # encodePriority shifts it again to 64. No. Pass 0 for facility, then
        # do our own bitwise or.
        pri = self.encodePriority(0, syspri) | self.facility

        # Point syslog at our file. Syslog module remains pointed at our
        # log file until any other call to syslog.openlog(), such as those
        # in p4gf_audit_log.py.
        #
        # We have to re-open over and over because p4gf_auth_log.py also
        # calls syslog.openlog(), which clobbers our debug log setting.
        syslog.openlog(_syslog_ident, syslog.LOG_PID)

        syslog.syslog(pri, msg)


def _config_for_syslog(basic_config, config_line):
    '''
    Modify basic_config dict to write to syslog instead of file.

    config_line is the portion of the config file line after the colon:
        "syslog [address]
    '''
    w = config_line.split()
    if 2 <= len(w):
        basic_config['handler'] = P4GFSysLogHandler(address=w[1])
    else:
        basic_config['handler'] = P4GFSysLogHandler()

    # Logging to syslog means no format, no file.
    for k in ['format','datefmt']:
        if k in basic_config:
            del basic_config[k]
    basic_config['filename'] = '/dev/null'

def configure_from_file(filename):
    """Read a p4gf_log.py config file and honor its settings."""

    global _configured
    _configured = True

    # Accumulate multiple logging.basicConfig() values so we can set
    # them all at once later rather than call basicConfig() over and
    # over, clobbering values set previously.
    basic_config = default_config()

    key_func = {
        'file'    : lambda val: basic_config.__setitem__('filename', val),
        'filename': lambda val: basic_config.__setitem__('filename', val),
        'format'  : lambda val: basic_config.__setitem__('format', val),
        'datefmt' : lambda val: basic_config.__setitem__('datefmt', val),
        # 'root'    : lambda val: basic_config.__setitem__('root', val),
        }

    with open(filename, 'r') as f:
        line_array = f.readlines()
    line_number = 1
    for line in line_array:
        #sys.stderr.write("# Line: {}".format(line))
        line_number += 1
        line = line.strip()
        if not len(line):
            continue
        if (line[0] == '#'):
            continue
        separator_index = line.find(':')
        if (separator_index <= 1):
            print("# {filename}:{line_number} : Missing colon-terminated keyword. {line}"
                  .format(filename=filename, line_number=line_number, line=line))
            continue
        key = line[:separator_index].strip()
        val = line[separator_index + 1:].strip()

        if key in key_func:
            key_func[key](val)
        elif key == 'handler':
            if val.startswith('syslog'):
                _config_for_syslog(basic_config, val)
            elif val == 'console':
                basic_config['handler'] = logging.StreamHandler()
        else:
            _print("set {key} to {val}".format(key=key, val=val.upper()))
            logging.getLogger(key).setLevel(val.upper())

    if 'filename' in basic_config:
        # perform variable substitution on file path
        frmargs = {}
        frmargs['user'] = os.path.expanduser('~')
        frmargs['tmp'] = tempfile.gettempdir()
        basic_config['filename'] %= frmargs
    logging.basicConfig(**basic_config)

    if 'handler' in basic_config:
        logging.getLogger().addHandler(basic_config['handler'])
    #logging.getLogger().setLevel(basic_config['root'].upper())

    #_print("setroot requested={req} got={num}/{name}"
    #       .format(req=basic_config['root'].upper(),
    #                           num=logging.getLogger().getEffectiveLevel(),
    #                           name=logging.getLevelName(logging.getLogger()
    #                                                     .getEffectiveLevel())))


def script_name():
    """Return the 'p4gf_xxx' portion of argv[0] suitable for use as a log category."""
    return sys.argv[0].split('/')[-1]


def default_config():
    """Return the settings we use unless config file says otherwise."""
    cfg = { 'filename' : os.environ['HOME'] + '/p4gf_log.txt',
            'format'   : '%(asctime)s %(name)-10s %(levelname)-8s %(message)s',
            'datefmt'  : '%m-%d %H:%M:%S',
            #'root'     : 'WARNING',
            }
    return cfg


def configure_from_defaults():
    """Load a default configuration if no config file available."""
    cfg = default_config()
    logging.basicConfig(**cfg)
    #logging.getLogger().setLevel(cfg['root'].upper())

    global _configured
    _configured = True

class ExceptionLogger:
    """A handler that records all exceptions to log instead of to console.

    with p4gf_log.ExceptionLogger() as dont_care:
        ... your code that can raise exceptions...
    """

    # pylint:disable=C0301
    # line too long
    def __init__(self, exit_code_array=None, category=script_name(), squelch=True, write_to_stderr_=False):
        """
        category, if specified, controls where exceptions go if caught.
        squelch controls the return value of __exit__, which in turn
        controls what happens after reporting a caught exception:

        squelch = True: squelch the exception.
            This is what we want if we don't want this exception
            propagating to console. Unfortunately this also makes it
            harder for main() to know if we *did* throw+report+squelch
            an exception.

        squelch = False: propagate the exception.
            This usually results in dump to console, followed by the
            death of your program.

        """
        _print("ExceptionLogger.__init__")
        self.__category__ = category
        self.__squelch__ = squelch
        self.__write_to_stderr__ = write_to_stderr_
        if exit_code_array:
            self.__exit_code_array__ = exit_code_array
        else:
            self.__exit_code_array__ = [1]
        _lazy_init()

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, _traceback):
        """Record any exception to log. NOP if no exception."""

        # Someone called sys.exit(x). Retain the exit code.
        if isinstance(exc_value, SystemExit):
            self.__exit_code_array__[0] = exc_value.code
            return self.__squelch__

        if exc_type:
            log = logging.getLogger(self.__category__)
            log.error("Caught exception", exc_info=True)
            val = exc_value.args[0] if exc_value.args else exc_value
            if self.__write_to_stderr__:
                sys.stderr.write('{}\n'.format(val))

        return self.__squelch__


def caller(depth=1):
    """Return a dict for the caller N frames up the stack."""
    stack = inspect.stack()
    if len(stack) <= depth:
        if len(stack) == 0:
            return
        depth = 1
    frame = stack[depth]
    fname = os.path.basename(frame[1])
    frame_dict = { 'file'     : fname,
                   'filepath' : frame[1],
                   'filebase' : os.path.splitext(fname)[0],
                   'line'     : frame[2],
                   'func'     : frame[3],
                 }
    # Internally sever link to Traceback frame in an attempt to avoid
    # module 'inspect' and its refcount cycles.
    del frame
    return frame_dict


def for_module(depth=2):
    """Return the logger for the calling module.

    Returns "p4gf_foo" for module /Users/bob/p4gf_foo.py. This simple
    punctuation-less name works better as a log category.

    Typically this is called at the top of your Python script:

        LOG = p4gf_log.for_module()

    and then used later as a logger:

        LOG.debug("hello")

    The depth parameter tells for_module() how many stack frames up to
    search for the caller's filename. The default of two is usually what you
    want.

    """
    c = caller(depth)
    return logging.getLogger(c['filebase'])


def run_with_exception_logger(func, write_to_stderr=False):
    """Wrapper for most 'main' callers, route all exceptions to log."""

    exit_code = [1]
    c = None
    log = None
    with ExceptionLogger(exit_code, write_to_stderr_=write_to_stderr):
        c = caller(2)
        log = logging.getLogger(c['filebase'])
        log.debug("{file}:{line} start --".format(file=c['file'],
                                                  line=c['line']))
        exit_code[0] = func()

    if log and c:
        log.debug("{file}:{line} exit={code} --".format(code=exit_code[0],
                                                        file=c['file'],
                                                        line=c['line']))
    exit(exit_code[0])


def _lazy_init():
    """If we have not yet called configure_from_file(), do so now, using a
    default set of config settings.
    """
    if not _configured:
        try:
            config_file_path = find_config_file()
            if (config_file_path):
                configure_from_file(config_file_path)
            else:
                configure_from_defaults()

        # pylint: disable=W0702
        # W0702 No exception type(s) specified
        # Yes, I want ALL the exceptions.
        except:
            # Unable to open log file for write? Some other random error?
            # Printf and squelch.
            import traceback; traceback.print_exc()
            sys.stderr.write("Git Fusion: Unable to configure log.\n")
            sys.stderr.write(traceback.format_exc(0))


def _print(msg):
    """Internal debugging printf debugging."""
    if _enable_print:
        print("### " + msg)


def _arg_pop(cmdlist):
    """Pop the next command, and up to one optional category, return as a tuple"""
    if len(cmdlist) == 0:
        return (None, None)

    level = cmdlist.pop(0)

    if len(cmdlist) == 0 or cmdlist[0] in _level_choice_list:
        return (level, None)

    category = cmdlist.pop(0)
    return (level, category)


def _curr_level(cat=None):
    """Return the a string like '10/DEBUG' for the current logging level
    for the given category

    Debugging dump shorthand.
    """
    if (cat):
        num = logging.getLogger(cat).getEffectiveLevel()
    else:
        num = logging.getLogger().getEffectiveLevel()
    return "{num}/{name}".format(num=num, name=logging.getLevelName(num))


def _parse_argv():
    """Build and return a dict of test options from argv"""

    import argparse

    parser = argparse.ArgumentParser("Internal test. Load config, then record a single event")
    parser.add_argument('--config',
                        metavar="cfg_file",
                        default=_config_filename_default,
                        nargs=1,
                        help='load the named config file, default is "{default}"'
                             .format(default=_config_filename_default))
    parser.add_argument('--noconfig',
                        action='store_true',
                        default=None,
                        help='load no config, not even the default config file.')

    parser.add_argument('cmdlist',
                        metavar='cmdlist',
                        nargs='+',
                        help='one or more event levels to log. raise throws a RuntimeError().'
                             +'\nOne of ({choices}), optionally followed by a category such as'
                             +' p4gf_init or p4gf_object_type.'
                             .format(choices=_level_choices))
    args = parser.parse_args()

    if args.noconfig:
        args.config = None
    del args.noconfig

    # Convert from argparse's undesirable list to more useful string.
    if args.config:
        args.config = ''.join(args.config)
    return args


def _config(args):
    """Load config file if specified"""

    try:
        if (args.config):
            configure_from_file(args.config)
        else:
            configure_from_defaults()

    # pylint: disable=W0702
    # W0702 No exception type(s) specified
    # Yes, I want ALL the exceptions.
    except:
        # Unable to open log file for write? Some other random error?
        # Printf and squelch.
        sys.stderr.write("Git Fusion: Unable to configure log.\n")
        sys.stderr.write(traceback.format_exc(0))


def _process_next_arg(args):
    """Record one event and return True, or do nothing and return False if nothing more to do."""

    (level, category) = _arg_pop(args.cmdlist)
    if not level:
        return False

    with ExceptionLogger():
        if (level == "raise"):
            raise RuntimeError("raise")

        if (category):
            getattr(logging.getLogger(category), level)("message-category")
            _print("record cat={cat} curr_lvl={curr} event={event}"
                   .format(cat=category, curr=_curr_level(category), event=level))
        else:
            getattr(logging, level)("message-root")
            _print("record root curr_lvl={curr} event={event}"
                   .format(curr=_curr_level(), event=level))
    return True


def _main():
    """Test harness for log"""
    # print "Input: " + " ".join(sys.argv[1:])

    args = _parse_argv()
    _config(args)

    while _process_next_arg(args):
        pass


if __name__ == '__main__':
    _main()

