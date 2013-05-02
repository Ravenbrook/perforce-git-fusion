#! /usr/bin/env python3.2

"""Acquire and release a lock using p4 counters."""

import calendar
import logging
import math
import multiprocessing
import os
import sys
import time
import threading

from P4 import P4

import p4gf_create_p4
import p4gf_const
import p4gf_util

LOG = logging.getLogger(__name__)

# time.sleep() accepts a float, which is how you get sub-second sleep durations.
MS = 1.0 / 1000.0

# How often we retry to acquire the lock.
_RETRY_PERIOD = 100 * MS

# If we cannot acquire the lock within 10 seconds, give up. Someone else
# is hogging the lock, and it's better to give up than to sit here
# forever, holding the git user hostage.
#
# Bad: it is WORSE to give up too fast, and 10 seconds is not that
# long to wait if we just finished pushing a 15-minute-long giant push.
# You're going to make me retransmit ALL of that again? Grr.)
LOCK_TIMEOUT_SECS = 10

# Number of seconds after which we assume the process holding the lock has
# exited without deleting the counter. This makes several assumptions:
# 1) Process will always regularly update its heartbeat counter
# 2) Clocks on both systems are reasonably in sync
HEARTBEAT_TIMEOUT_SECS = 60

# Rate for updating heartbeat counter, in seconds
HEART_RATE = 1


class CounterLock:
    """An object that acquires a lock when created, releases when
    destroyed. If the auto_beat constructor argument is true, then
    the heartbeat counter will be updated on a regular basis via a
    subprocess.

    with p4gf_lock.CounterLock(p4, "mylock") as lock:
        ... do stuff ...
        # Call periodically to ward off any future watchdog timer,
        # unless auto_beat() was called earlier.
        lock.update_heartbeat()
    """

    def __init__( self
                , p4
                , counter_name
                , timeout_secs   = LOCK_TIMEOUT_SECS
                , heartbeat_only = False):
        self.__p4__              = p4
        self.__counter_name__    = counter_name
        self.__has__             = False
        self.__timeout_secs__    = timeout_secs
        self.__log_timer         = None
        self.__acquisition_time  = None
        self.__heartbeat_time    = None
        self.__heartbeat_content = None
        self.__heartbeat_only    = heartbeat_only
        self.__auto_beat         = False
        self.__event             = None

    def __enter__(self):
        self.acquire(self.__timeout_secs__)
        return self

    def __exit__(self, exc_type, exc_value, _traceback):
        """If we own the lock, release it."""
        self.release()
        return False    # False = do not squelch exception

    def counter_name(self):
        """The lock."""
        return self.__counter_name__

    def heartbeat_counter_name(self):
        """Who owns the lock."""
        return "{counter}_heartbeat".format(counter=self.counter_name())

    def _acquire_attempt(self):
        """Attempt an atomic increment. If the result is 1, then we now
        own the lock. Any other value means somebody else owns the
        lock.
        """
        value = p4gf_util.first_value_for_key(
                self.__p4__.run('counter', '-u', '-i', self.counter_name()),
                'value')
        acquired = value == "1"  # Compare as strings, "1" != int(1)
        LOG.debug("_acquire_attempt {name} pid={pid} acquired={a} value={value}"
                  .format(pid=os.getpid(),
                          a=acquired,
                          name=self.counter_name(),
                          value=value))
        return acquired

    def acquire(self, timeout_secs=None):
        """Block until we acquire the lock or run out of time.

        timeout_secs:
            None or 0 means forever.
            negative means try once then give up.
        """
        if self.__heartbeat_only:
            return

        start = time.time()
        while True:
            self.__has__ = self._acquire_attempt()
            if self.__has__:
                self.__acquisition_time = time.time()
                self.update_heartbeat()
                self._start_pacemaker()
                self._create_log_timer()
                self._test_view_sleep_after_acquire()
                return

            # Check on the lock holder's status, maybe clear the lock.
            holder = self.get_heartbeat()
            if holder and not check_holder_alive(holder):
                LOG.debug("releasing the abandoned lock {}".format(holder))
                # Pretend we have the lock so we can release it.
                self.__has__ = True
                self.release()
                # Skip the timeout logic and loop around immediately.
                continue

            # Stop waiting if run out of time. Tell the git user
            # who's hogging the lock.
            elapsed = time.time() - start
            if timeout_secs and timeout_secs <= elapsed:
                holder = self.get_heartbeat()
                msg = ('Unable to acquire lock: {}'
                       .format(self.counter_name()))
                if holder:
                    msg += '\nLock holder: {}'.format(holder)
                timeout = int(math.ceil(HEARTBEAT_TIMEOUT_SECS / 60.0))
                msg += '\nPlease try again after {0} minute(s).'.format(timeout)
                raise RuntimeError(msg)

            time.sleep(_RETRY_PERIOD)

    def _start_pacemaker(self):
        """If the lock has been acquired and it is configured to have
        automatic updates of the heartbeat counter, then set up a child
        process to regularly update the heartbeat.
        """
        if self.__auto_beat and self.__event is None:
            # Set up event flag for signaling subprocess to exit.
            self.__event = multiprocessing.Event()
            # Start the pacemaker to beat the heart automatically.
            p = multiprocessing.Process(target=pacemaker,
                    args=[self.__counter_name__, self.__event])
            p.daemon = True
            p.start()

    def _stop_pacemaker(self):
        """If the pacemaker has been set up, signal it to stop.
        """
        if self.__event is not None:
            # Signal the pacemaker to exit normally.
            self.__event.set()
            self.__event = None

    def _held_duration_seconds(self):
        '''
        How long since we acquired this lock?
        '''
        if not self.__acquisition_time:
            return 0
        return time.time() - self.__acquisition_time

    # pylint: disable=R0201
    # R0201 Method could be a function
    # Yeah it could. But it's not going to be.
    def _test_view_sleep_after(self, key):
        '''
        Some testing code needs us to   s l o w   d o w n
        so that the test can notice the counter change or
        introduce some state change.
        '''
        test_vars = p4gf_util.test_vars()
        if not key in test_vars:
            return

        sleep_seconds = test_vars[key]
        LOG.debug("Test: sleeping {} seconds...".format(sleep_seconds))
        time.sleep(float(sleep_seconds))
        LOG.debug("Test: sleeping {} seconds done".format(sleep_seconds))

    def _test_view_sleep_after_acquire(self):
        '''
        Some testing code wants to force a sleep after view lock acquisition.
        '''
        if not "view" in self.counter_name():
            return

        self._test_view_sleep_after(p4gf_const
                               .P4GF_TEST_LOCK_VIEW_SLEEP_AFTER_ACQUIRE_SECONDS)

    def release(self):
        """If we have the lock, release it. If not, NOP."""
        if self.has() or self.__heartbeat_only:
            self._stop_pacemaker()
        if not self.has():
            return False

        LOG.debug("release {name} pid={pid}"
                  .format(pid=os.getpid(),
                          name=self.counter_name()))

        self._destroy_log_timer()

        if _log_timer_duration_seconds() <= self._held_duration_seconds():
            self._report_long_lock()
            LOG.warning("Released lock {}".format(self.counter_name()))
        self.clear_heartbeat()
        self.__p4__.run('counter', '-u', '-d', self.counter_name())
        self.__has__ = False

        return True

    def has(self):
        """Do we have the lock? False if we timed out or error."""
        return self.__has__

    def autobeat(self):
        """Enable keeping the heartbeat counter updated automatically via
        a subprocess. This applies to the next acquisition of the lock.
        """
        self.__auto_beat = True

    def heartbeat_content(self):
        '''
        What should we write to our heartbeat counter?
        Enough data that an admin could figure out who's hogging the lock.

        If enough time has elapsed since the last call to this function,
        updates the content string.

        Returns a tuple containing the current and previous content strings.
        '''
        # get the seconds since the epoch in UTC
        now = calendar.timegm(time.gmtime())
        if self.__heartbeat_time and now - self.__heartbeat_time < HEART_RATE:
            return self.__heartbeat_content, self.__heartbeat_content

        self.__heartbeat_time = now

        # tabs are used as separators for easy parsing in check_holder_alive()
        val = "{host}\t{process}\t{time}\t{argv}" \
              .format( host     = p4gf_util.get_hostname()
                     , process  = os.getpid()
                     , time     = int(self.__heartbeat_time)
                     , argv     = ' '.join(sys.argv)
                     )
        last = self.__heartbeat_content
        self.__heartbeat_content = val
        return self.__heartbeat_content, last

    def update_heartbeat(self):
        '''
        Update the timestamp written to our heartbeat counter, if we own the
        lock, or if this is a heartbeat-only lock.
        '''
        if not (self.has() or self.__heartbeat_only):
            return

        # don't update counter or log if nothing has changed
        current, last = self.heartbeat_content()
        if current != last:
            self.__p4__.run('counter', '-u', self.heartbeat_counter_name(), current)
            LOG.getChild("heartbeat").debug("update_heartbeat {name} {val}"
                 .format(name=self.heartbeat_counter_name(),
                         val=current))

    def get_heartbeat(self):
        '''
        Return the current heartbeat value, if any.

        Might provide clue to what holds the lock.
        '''
        value = p4gf_util.first_value_for_key(
                    self.__p4__.run('counter', '-u', self.heartbeat_counter_name()),
                    'value')
        if not value or value == '0':
            return None
        return value

    def clear_heartbeat(self):
        '''
        Clear the heartbeat counter associated with this lock.
        '''
        # Suppress P4 exceptions here.
        # Don't care if fail, only lock counter matters.
        with self.__p4__.at_exception_level(P4.RAISE_NONE):
            self.__p4__.run('counter', '-u', '-d', self.heartbeat_counter_name())

    def _log_timer_expired(self, timer_id):
        '''
        We've held our lock for a long time. Tell the log.
        '''

        LOG.debug("_log_timer_expired id={}".format(timer_id))
        self._report_long_lock()

        # Restart timer: We'll log the same message again in N seconds.
        self._destroy_log_timer()
        self._create_log_timer()

    def _report_long_lock(self):
        '''
        Unconditionally record our lock duration to log at level WARNING.
        '''
        LOG.warning("Lock {lock_name} held for {duration_seconds} seconds by {holder}"
                .format( lock_name        = self.counter_name()
                       , duration_seconds = int(self._held_duration_seconds())
                       , holder           = self.heartbeat_content()[0]))

    def _create_log_timer(self):
        '''
        Return a one-shot timer, already started, that will call
        _log_timer_expired() once in N seconds.
        '''
        duration_seconds = _log_timer_duration_seconds()
        timer_id = _next_timer_id()
        LOG.debug("Long-held locks reported every {} seconds. Timer id={}"
                  .format(duration_seconds, timer_id))
        t = threading.Timer( int(duration_seconds)
                           , CounterLock._log_timer_expired
                           , args=[self, timer_id])
        t.start()
        self.__log_timer = t

    def _destroy_log_timer(self):
        '''
        Kill the timer or we'll end up sitting here for 5 minutes waiting
        for a timer to complete.
        '''
        if self.__log_timer:
            timer_id = self.__log_timer.args[1]
            LOG.debug("Canceling long-held lock reporter timer. Timer id={}"
                      .format(timer_id))
            self.__log_timer.cancel()
            self.__log_timer = None

    def canceled(self):
        '''
        Has our lock counter been cleared?

        This is one way to remote-kill a long-running Git Fusion task.
        '''
        value = p4gf_util.first_value_for_key(
                        self.__p4__.run('counter', '-u', self.counter_name()),
                        'value')

        if value != "0":  # Compare as strings, "0" != int(0)
            return False

        LOG.error("Lock canceled: {name}={value}"
                  .format(name=self.counter_name(), value=value))
        return True


def pacemaker(view_name, event):
    """As long as event flag is clear, update heartbeat of named lock.
    """
    # Running in a separate process, need to establish our own P4 connection
    # and set up a heartbeat-only lock to update the heartbeat of the lock
    # associated with the view.
    p4 = p4gf_create_p4.connect_p4(client=p4gf_util.get_object_client_name())
    with p4:
        lock = CounterLock(p4, view_name, heartbeat_only=True)
        LOG.debug("starting pacemaker for lock {}".format(view_name))
        while not event.is_set():
            lock.update_heartbeat()
            event.wait(HEART_RATE)
        lock.clear_heartbeat()
        LOG.debug("stopping pacemaker for lock {}".format(view_name))


def check_holder_alive(holder):
    """Compares the time value in the lock contents to the current time
    on this system (clocks must be synchronized closely!) and if the
    difference is greater than HEARTBEAT_TIMEOUT_SECS then assume the lock
    holder has died.

    Returns True if lock is still valid, and False otherwise.
    """
    try:
        then = int(holder.split('\t', 3)[2])
    except ValueError:
        LOG.warn("malformed heartbeat counter contents: {}".format(holder))
        return True
    now = calendar.timegm(time.gmtime())
    return now < then or (now - then) < HEARTBEAT_TIMEOUT_SECS


_timer_id = 0


def _next_timer_id():
    '''
    Give each timer a unique identifier.
    '''
    global _timer_id
    _timer_id += 1
    return _timer_id


def _log_timer_duration_seconds():
    '''
    How long should we wait before logging reports about long-held locks?
    '''
    test_vars = p4gf_util.test_vars()
    if p4gf_const.P4GF_TEST_LOCK_LOG_AFTER_HELD_SECONDS in test_vars:
        return float(test_vars[p4gf_const.P4GF_TEST_LOCK_LOG_AFTER_HELD_SECONDS])
    return float(5 * 60)


def view_lock_name(view_name):
    '''
    Return a name for a counter that we use to lock a view.
    '''
    return "git_fusion_view_{}_lock".format(view_name)


def view_lock(p4, view_name):
    '''
    Return a lock for a single view.
    '''
    lock = CounterLock(p4, view_lock_name(view_name))
    lock.autobeat()
    return lock


def view_lock_heartbeat_only(p4, view_name):
    '''
    Return a lock that only updates an existing heartbeat.
    Does not acquire or release the lock.
    Assumes someone else holds the lock. Does not check for this.
    '''
    return CounterLock( p4
                      , view_lock_name(view_name)
                      , heartbeat_only=True)
