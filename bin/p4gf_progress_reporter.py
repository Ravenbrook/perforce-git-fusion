#! /usr/bin/env python3.2
'''
Tools for telling the user what is going on.
'''

import math
import sys
import time

class ProgressReporter:
    '''
    An object that can write single status messages and sequences of
    progress messages to the git client.
    '''

    def __init__(self):
        self.enabled                = True
        self.indeterminate          = False
        self.progress_nominator     = 0
        self.progress_denominator   = 0
        self.debug                  = False
        
    def status(self, message):
        '''
        Write a single status message.
        '''
        if not self.enabled:
            return
        sys.stderr.write("Perforce: {}\n".format(message))
    
    def progress_init_determinate(self, denominator):
        '''
        Tell me how many progress increments you plan on taking.

        Does not write any status messages. Call progress_increment() for that.
        '''
        self.indeterminate        = False
        self.progress_nominator   = 0
        self.progress_denominator = denominator
        
    def progress_init_indeterminate(self):
        '''
        Set up progress reporter with no known endpoint.

        Does not write any status messages. Call progress_increment() for that.
        '''
        self.indeterminate        = True

    def progress_increment(self, message):
        '''
        If determinate, show "1/25 doing things..." where 25 is the denominator
        set in a previous call to progress_init_determinate().

        If indeterminate, progress message will just report the nominator:
        "Copying files: nnn".

        Does not range-check! You can end up looking silly with "26/25" or
        "1/0" if you overstep your bounds.
        '''
        self.progress_nominator += 1
        if not self.enabled:
            return

        if self.indeterminate:
            progress_str = "Perforce: %s: %d" % (message, self.progress_nominator)
        else:
            fmt = ("Perforce: %3d%% (%{ct}d/%{ct}d) %s"
                   .format(ct=self._digit_count(self.progress_denominator)))
            progress_str = fmt % (self.percentage(),
                                 self.progress_nominator,
                                 self.progress_denominator,
                                 message)
        sys.stderr.write('\r' + progress_str)
        
        if not self.indeterminate and self.progress_denominator <= self.progress_nominator:
            self.progress_finish()
    
        if self.debug:
            time.sleep(1)
        
    # pylint: disable=R0201
    # R0201 Method could be a function
    def progress_finish(self):
        '''
        Send newline to stderr upon completion of work item being reported.

        This is called automatically for determinate progress reporters, but
        must be called explicitly for INDETERMINATE progress reporters.
        '''
        sys.stderr.write('\n')

    # pylint: disable=R0201
    # R0201 Method could be a function
    def _digit_count(self, n):
        '''
        How many digits?
        '''
        return 1 + int(math.log10(n))
            
    def percentage(self):
        '''
        Return an integer 0..100
        
        Does range-check. n/0 = 0, 26/25 = 100.
        '''
        if not self.progress_denominator:
            return 0
        
        if self.progress_denominator <= self.progress_nominator:
            return 100
        
        return int(  float(self.progress_nominator) * 100.0
                   / float(self.progress_denominator) )
