#! /usr/bin/env python3.2
"""profiling classes"""

import time
import weakref

class Counter:
    """a class for counting things, for performance measurement"""
    def __init__(self, name, units=''):
        self.name = name
        self.units = units
        self.value = 0

    def __iadd__(self, other):
        if isinstance(other, int):
            self.value += other
        return self

    def __str__(self):
        return "{:35}: {:8} {}".format(self.name, self.value, self.units)

class Timer:
    """simple class for timing code"""
    def __init__(self, name, parent=None):
        self.name = name
        if parent:
            self.parent = weakref.ref(parent)
            self.parent().children.append(self)
        else:
            self.parent = None
        self.time = 0
        self.child_time = 0
        self.children = []
        self.start = 0
        self.active = False

    def __float__(self):
        return self.time

    def __enter__(self):
        assert not self.active
        assert not self.parent or self.parent().active
        self.active = True
        self.start = time.time()

    def __exit__(self, _exc_type, _exc_value, _traceback):
        assert self.active
        assert not self.parent or self.parent().active
        self.active = False
        delta = time.time() - self.start
        self.time += delta
        if self.parent:
            self.parent().child_time += delta

    def do_str(self, indent):
        """helper function for str(), recursively format timer values"""
        items = [" " * indent + "{:25}".format(self.name) + " " * (10 - indent) +
                 ": {:8.4f} seconds".format(self.time)]
        indent += 2
        if len(self.children):
            self_time = self.time - self.child_time
            items.append(" " * indent + "{:25}".format("self time") + " " * (10 - indent) +
                         ": {:8.4f} seconds".format(self_time))
        for t in self.children:
            items.append(t.do_str(indent))
        return "\n".join(items) 

    def __str__(self):
        return self.do_str(0)


class TimerCounterSet:
    """a collection of timers and counters for performance measurement"""
    def __init__(self):
        # keep lists for reporting order, dicts for fast update access
        self.timers = []
        self.timer = {}
        self.counters = []
        self.counter = {}

    def add_counter(self, name, units=''):
        """add a single counter with given name and optional units"""
        self.counters.append(name)
        self.counter[name] = Counter(name, units)

    def add_counters(self, counters):
        """add a list of counters, each element being either a name
        or a tuple (name, units)
        """
        for c in counters:
            if isinstance(c, tuple):
                self.add_counter(c[0], c[1])
            else:
                self.add_counter(c)

    def add_timer(self, name, parent=None):
        """add a single timer with given name, and optionally named parent"""
        # top level timer? just add
        if not parent:
            self.timers.append(name)
            self.timer[name] = Timer(name)
            return
        # child timer, add to parent and dict, but not in list
        # it will be reported by its parent
        self.timer[name] = Timer(name, self.timer[parent])

    def add_timers(self, timers):
        """add a list of timers, each element being either a name
        or a tuple (name, parent_timer_name)
        """
        for t in timers:
            if isinstance(t, tuple):
                self.add_timer(t[0], t[1])
            else:
                self.add_timer(t)

    def __str__(self):
        return "\n".join([str(self.timer[t]) for t in self.timers] +
                         [str(self.counter[c]) for c in self.counters])
