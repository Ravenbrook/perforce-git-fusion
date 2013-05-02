#! /usr/bin/env python3.2
"""Code for detecting conflicting Perforce changelist submitted while
copying git commits to Perforce.
"""
from   collections import namedtuple
import logging

import p4gf_log
import p4gf_object_type
import p4gf_path
import p4gf_util

LOG = p4gf_log.for_module()


# pylint: disable=C0103
# C0103 Invalid name
# Yeah, because it's a named tuple which is pretty much a struct/class and
# thus deserves a capital.
CommitChange = namedtuple('CommitChange', ['git_commit_sha1', 'p4_changelist_number'])


class G2PConflictChecker:
    """An object that follows along as you copy commits from git to
    Perforce, and knows how to detect when someone else has submitted to
    Perforce, causing a conflict.
    """

    def __init__(self, ctx,
                 testing_head_sha1=None,    # Only for testing, set to bypass
                                            # p4gf_object_type.sha1_to_object_type(),
                                            # set to -1 to leave self.good[] empty.
                 testing_head_change=None): # Only for testing
        self.ctx = ctx

        LOG.debug("checker init")
        # List of CommitChange tuples.
        #
        # As we copy from git to Perforce, record the most recent git
        # commit we just copied to Perforce, and the Perforce changelist
        # number that corresponds with that change. If we see any
        # changes other than this in our view, we know someone else has
        # submitted to our view and that we hit a conflict.
        #
        # element[0] is usally the git commit sha1 and Perforce
        # changelist number that correspond with HEAD at __init__ time.
        self.good                    = []

        # Updated by check(), point to element of good[] or None if no
        # conflict yet.
        self.first_conflict_index    = None

        # Updated by find_conflict_index() when no conflict is found.
        self.last_good_change_number = None

        # Dump out starting state.
        if LOG.isEnabledFor(logging.DEBUG):
            cmd = ['git', 'log', '--oneline', '--all', '--decorate', '-5']
            d = p4gf_util.popen_no_throw(cmd)
            LOG.debug('dumping current state:')
            LOG.debug('{}\n{}'.format(' '.join(cmd), d['out']))
            with ctx.p4.while_tagged(False):
                cmd = ['changes', '-m', '5', '-c', ctx.config.p4client]
                r = ctx.p4.run(cmd)
                LOG.debug('p4 {}\n{}'
                          .format( ' '.join(cmd)
                                 , '\n'.join(r)  ))

        if testing_head_sha1 != None or testing_head_change != None:
            # unit test hook to bypass p4gf_object_type.sha1_to_object_type()
            if testing_head_sha1 != -1:
                self.good.append(CommitChange(testing_head_sha1,
                                              testing_head_change))
        else:
            LOG.debug("checker getting head sha1")
            head_sha1 = p4gf_util.git_head_sha1()
            if head_sha1:
                object_type = p4gf_object_type.sha1_to_object_type(
                                          sha1      = p4gf_util.git_head_sha1()
                                        , view_name = ctx.config.view_name
                                        , p4        = ctx.p4)
                LOG.debug("checker got head sha1")
                if (object_type.type == 'commit'):
                    LOG.debug("checker got commit {}".format(object_type))
                    change_num = object_type.view_name_to_changelist(ctx.config.view_name)   
                    self.good.append(CommitChange(object_type.sha1, change_num))
                    self.last_good_change_number = change_num

        LOG.debug("end of __init__(): {}".format(self))


    def __str__(self):
        s = ("first_conflict_index={} last_good_change_number={} good.ct={}"
             .format(self.first_conflict_index
                    ,self.last_good_change_number
                    ,len(self.good)))
        if len(self.good):
            s = s  + "\n" \
              + "\n".join(["{} {}".format(nt.git_commit_sha1,
                                          nt.p4_changelist_number)
                           for nt in self.good])
        return s


    def record_commit(self, commit_sha1, changelist_number):
        """A single git commit has been copied and submitted to Perforce.

        Record that commit's sha1 and submitted changelist number.
        """
        LOG.debug("record_commit g={g} p={p}".format(g=commit_sha1, p=changelist_number))
        if (not commit_sha1) and (not changelist_number):
            raise RuntimeError("Cannot record a partial git commit + "
                               + "p4 changelist, both must be non-None")
        self.good.append(CommitChange(commit_sha1, changelist_number))
        # Do NOT update last_unconflicted_index here! We don't _know_
        # another Perforce changelist snuck in ahead of this commit thus
        # making this a conflicted, ungood commit. Let check() do that.
        LOG.debug("end of record_commit(): {}".format(self))


    def find_conflict_index(self, changelist_list_):
        """If changelist_list contains ANY changelists that were not also
        recorded via record_commit(), then they must have come from elsewhere
        and are a conflict.

        Return an index into g2p_change_number identifying the first commit/change
        that occurs at or after a conflict. This commit does NOT exactly match
        its Perforce changelist counterpart and cannot be considered
        "committed". This is the first commit that 'git push' must reject.
        'git push' must accept (and move the head pointer to) the commit
        immedately before this commit.
        """

        # p4 changes returns newer-to-older [4, 3, 2, 1] but our loop works
        # better older-to-newer [1, 2, 3, 4].
        changelist_list = changelist_list_
        changelist_list.reverse()

        # Pull from "known good" and "p4 changes output" in lock step until we
        # hit a mismatch. Should be O(n)
        good_index = 0
        good    = [x.p4_changelist_number for x in self.good]
        changes = [x['change'] for x in changelist_list]
        LOG.debug("find_conflict_index changes={}".format(changes))
        LOG.debug(str(self))
        
        # Skip old "good" elements that occur before the start of "changes" history.
        while len(good) and not (good[0] in changes):
            good.pop(0)

        while len(good) and len(changes):
            g = good.pop(0)
            c = changes.pop(0)
            if (g == c):
                good_index += 1
                continue

            return good_index

        if len(good):
            # We fell off the end of one or both lists. If there are any known
            # changes left in good that did not appear in 'p4 changes' output
            # changes, then something's wrong. I guess we'll flag the first
            # unseen "good" as conflict.
            return good_index

        if len(changes):
            # There are one or more changes in 'p4 changes' that did not
            # appear in known, those are conflicts. Append the first to our
            # list of "known" just so that we can return an index to it.
            good.append(CommitChange(None, changes[0]))
            return good_index

        # Everything in good[] was also in changelist_list[], so it's all good.
        if (len(self.good)):
            self.last_good_change_number = self.good[-1].p4_changelist_number
        return None

    def check(self):
        """Query Perforce for recent changes, if any submitted changes
        are not ours, then a conflict has occurred and it is time to
        stop copying.

        Return None if no conflict, or first conflicting p4 changelist number.
        """

        # job058596: On play:1999, @now is NOT reporting the most recently
        # submitted changelist, causing 'p4 changes //client/....@change,now'
        # to not report the recent change, and find_Conflict_index() to
        # (correctly!) report a conflict because we claim there should be a
        # changelist in self.good but fail to see it in changes. Work around
        # this @now bug by using a date far in the future, but not so far that
        # Perforce rejects it as a bogus date. PS: Note to self: if we're
        # still using this code in the year 2030, try ',now' instead of
        # ',2030/12/31' and if that works, remove this hack.
        future = "2030/12/31"

        path = p4gf_path.slash_dot_dot_dot(self.ctx.config.p4client)
        path_at = None
        if self.last_good_change_number:
            path_at = ("{path}@{change},{future}"
                       .format(path=path,
                               change=self.last_good_change_number,
                               future=future))
        else:
            path_at = "{path}@0,{future}".format(path=path,
                                                 future=future)

        # -m5: don't fetch more than 5 changes. As long as we run
        # immediately before or after our git-to-perforce submit,
        # any conflict will be within the most recent 2 changes.
        cmd = ['changes', '-m5', '-ssubmitted', path_at]
        changelist_list = self.ctx.p4.run(cmd)
        LOG.debug('p4 changes -m5 -ssubmitted {} returned r={}'.format(path_at, changelist_list))

        self.first_conflict_index = self.find_conflict_index(changelist_list)

        if self.first_conflict_index == None:
            return None

        for i in range(0, len(self.good)):
            e = self.good[i]
            LOG.error("  {i}: g={g} p={p}"
                      .format(i=i, g=e.git_commit_sha1, p=e.p4_changelist_number))
        LOG.error("at index {}".format(self.first_conflict_index))

        return self.good[self.first_conflict_index].p4_changelist_number

    def has_conflict(self):
        """Has a previous call to check() detected a conflict?"""
        return None != self.first_conflict_index
