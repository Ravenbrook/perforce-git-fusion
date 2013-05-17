#! /usr/bin/env python3.2
"""copy_p4_changes_to_git"""

from collections import namedtuple
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zlib


from p4gf_fastimport import FastImport
from p4gf_p4file import P4File
from p4gf_p4changelist import P4Changelist
from p4gf_gitmirror import GitMirror
from p4gf_progress_reporter import ProgressReporter

import p4gf_profiler
import p4gf_util
import logging

from P4 import OutputHandler

LOG = logging.getLogger(__name__)


def expand_sha1(partial_sha1):
    """given partial SHA1 of a git object, return complete SHA1

    if there is no match, returns None
    """

    patt = re.compile(r'(?P<sha1>[0-9a-fA-F]{40,40}) (?P<type>[a-z]+) .*')
    cmd = ['git', 'cat-file', '--batch-check']
    result = p4gf_util.popen_no_throw(cmd, partial_sha1.encode())
    try:
        return patt.match(result['out']).group("sha1")
    except AttributeError:
        return None


class RevList:
    """alternative to dict of revisions
    searchable by depot_path#revision key

    It would be simpler to just use a dict, but due to the large number
    of revisions that we must handle, the memory cost of dict is excessive.

    Since all insertions are done before any lookups, just append
    to list and sort the list once when done inserting, rather than try
    to do sorted insertions.

    the list is built by PrintHandler, then must be sorted
    prior to using find in FstatHandler
    """
    def __init__(self):
        self.revs = []

    def append(self, p4file):
        """add a p4file to list of revs"""
        key = p4file.depot_path + '#' + p4file.revision
        self.revs.append((key, p4file))

    def sort(self):
        """sort list of revs by depot_path#revision"""
        self.revs.sort(key=lambda tup: tup[0])

    def find(self, key):
        """return P4File that matches key or None
        Uses bisection algorithm to search, which assumes list has been sorted.
        """
        lo = 0
        hi = len(self.revs)
        while lo < hi:
            mid = int((lo+hi)/2)
            midkey = self.revs[mid][0]
            if midkey <= key:
                if midkey == key:
                    return self.revs[mid][1]
                lo = mid + 1
            else:
                hi = mid
        return None


# pylint: disable=C0103
# C0103 Invalid name
# These names are imposed by P4Python

class FstatHandler(OutputHandler):
    """OutputHandler for p4 fstat, builds list of files.

    revs   : (input)  RevList
    changes: (output) dict["changeNum"]     ==> P4Changelist
    """
    def __init__(self, revs, changes):
        OutputHandler.__init__(self)
        self.revs = revs
        self.revs.sort()
        self.changes = changes

    def outputStat(self, h):
        """grab clientFile from fstat output"""
        key = h["depotFile"] + "#" + h["headRev"]
        p4file = self.revs.find(key)
        if p4file:
            p4file.client_path = h["clientFile"]
        else:
            # deleted files not reported by p4 print
            p4file = P4File.create_from_fstat(h)
            # ignore any deletions that happened before our starting change
            if not p4file.change in self.changes:
                LOG.debug("skipping deleted rev:{}".format(p4file.rev_path()))
                return OutputHandler.HANDLED
        self.changes[p4file.change].files.append(p4file)
        return OutputHandler.HANDLED


# pattern for unexpanding keywords
KEYWORD_PATTERN = re.compile(r'\$(?P<keyword>Author|Change|Date|DateTime'
                             + r'|File|Header|Id|Revision):[^$\n]*\$')
def unexpand(line):
    """unexpand a line from keyword expanded file"""
    return KEYWORD_PATTERN .sub(r'$\g<keyword>$', line.decode()).encode()


class PrintHandler(OutputHandler):
    """OutputHandler for p4 print, hashes files into git repo"""
    def __init__(self, need_unexpand, tempdir):
        OutputHandler.__init__(self)
        self.rev = None
        self.revs = RevList()
        self.need_unexpand = need_unexpand
        self.tempfile = None
        self.tempdir = tempdir
        self.progress = ProgressReporter()
        self.progress.progress_init_indeterminate()

    def outputBinary(self, h):
        """assemble file content, then pass it to hasher via queue"""
        self.appendContent(h)
        return OutputHandler.HANDLED

    def outputText(self, h):
        """assemble file content, then pass it to hasher via queue
        """
        b = bytes(h, 'UTF-8')
        self.appendContent(b)
        return OutputHandler.HANDLED

    def appendContent(self, h):
        """append a chunk of content to the temp file

        if server is 12.1 or older it may be sending expanded ktext files
        so we need to unexpand them

        It would be nice to incrementally compress and hash the file
        but that requires knowing the size up front, which p4 print does
        not currently supply.  If/when it does, this can be reworked to
        be more efficient with large files.  As it is, as long as the
        SpooledTemporaryFile doesn't rollover, it won't make much of a
        difference.

        So with that limitation, the incoming content is stuffed into
        a SpooledTemporaryFile.
        """
        if not len(h):
            return
        if self.need_unexpand and self.rev.is_k_type():
            h = unexpand(h)
        self.tempfile.write(h)

    def flush(self):
        """compress the last file, hash it and stick it in the repo

        Now that we've got the complete file contents, the header can be
        created and used along with the spooled content to create the sha1
        and zlib compressed blob content.  Finally that is written into
        the .git/objects dir.
        """
        if not self.rev:
            return
        size = self.tempfile.tell()
        self.tempfile.seek(0)
        compressed = tempfile.NamedTemporaryFile(delete=False, dir=self.tempdir)
        compress = zlib.compressobj()
        # pylint doesn't understand dynamic definition of sha1 in hashlib
        # pylint: disable=E1101
        sha1 = hashlib.sha1()

        # pylint:disable=W1401
        # disable complaints about the null. We need that.
        # add header first
        header = ("blob " + str(size) + "\0").encode()
        compressed.write(compress.compress(header))
        sha1.update(header)

        # then actual contents
        chunksize = 4096
        while True:
            chunk = self.tempfile.read(chunksize)
            if chunk:
                compressed.write(compress.compress(chunk))
                sha1.update(chunk)
            else:
                break
        # pylint: enable=E1101
        compressed.write(compress.flush())
        compressed.close()
        digest = sha1.hexdigest()
        self.rev.sha1 = digest
        blob_dir = ".git/objects/"+digest[:2]
        blob_file = digest[2:]
        blob_path = blob_dir+"/"+blob_file
        if not os.path.exists(blob_path):
            if not os.path.exists(blob_dir):
                os.makedirs(blob_dir)
            shutil.move(compressed.name, blob_path)
        self.rev = None

    def outputStat(self, h):
        """save path of current file"""
        self.flush()
        self.rev = P4File.create_from_print(h)
        self.revs.append(self.rev)
        self.progress.progress_increment('Copying files')
        LOG.debug("PrintHandler.outputStat() ch={} {}"
                  .format(h['change'], h["depotFile"] + "#" + h["rev"]))
        if self.tempfile:
            self.tempfile.seek(0)
            self.tempfile.truncate()
        else:
            self.tempfile = tempfile.TemporaryFile(buffering=10000000, dir=self.tempdir)
        return OutputHandler.HANDLED

    def outputInfo(self, _h):
        """outputInfo call not expected"""
        return OutputHandler.REPORT

    def outputMessage(self, _h):
        """outputMessage call not expected, indicates an error"""
        return OutputHandler.REPORT

class SyncHandler(OutputHandler):
    """OutputHandler for p4 sync -kf

    We run sync after clone for two reasons:

    1) when we're doing a push, git will report edits and adds of files
    both as 'M', but p4 needs to be told which are adds and which are edits.
    By keeping track of which files were in the last cloned changelist,
    we can distinguish adds and edits.  For this purpose we don't actually
    need the file content, just whether or not that file existed at that
    changelist.  So rather than populate our p4 client tree with actual
    content, we just maintain empty files for non-deleted revs.

    2) when it comes time to add content to perforce during a push, the
    server needs to think we've got the correct revs of files we want to
    edit.  Again, we don't need to actually have the content stored in
    the p4 client tree, as we'll supply that from the git repo as we're
    building up a change to submit.
    """

    def outputStat(self, h):
        """grab clientFile from fstat output"""
        p4file = P4File.create_from_sync(h)
        if p4file.is_delete():
            if os.path.exists(p4file.client_path):
                os.unlink(p4file.client_path)
        else:
            if not os.path.exists(p4file.client_path):
                if not os.path.exists(os.path.dirname(p4file.client_path)):
                    os.makedirs(os.path.dirname(p4file.client_path))
                with open(p4file.client_path, 'a'):
                    pass
        return OutputHandler.HANDLED
# pylint: enable=C0103

class RevRange:
    """Which Perforce changelists should we copy from Perforce to Git?

    If this is the first copy from Perforce to Git, identify a snapshot of
    history prior to our copy that we'll use as a starting point: the "graft"
    commit before our real copied commits start.
    """
    def __init__(self):

        # string. Perforce revision specifier for first thing to copy from
        # Perforce to Git. If a changelist number "@NNN", NNN might not
        # actually BE a real changelist number or a changelist that touches
        # anything in our view of the depot, and that's okay. Someone else
        # will run 'p4 describe //view/...{@begin},{end} to figure out the
        # TRUE changelist numbers.
        self.begin_rev_spec   = None

        # string.  Perforce revision specifier for last thing to copy from
        # Perforce to Git. Usually "#head" to copy everything up to current
        # time.
        self.end_rev_spec     = None

        # boolean. Is this the first copy into a new git repo? If so, then
        # caller must honor graft_change_num.
        self.new_repo         = False

        # integer. State of Perforce tree to copy into new_repo as a "graft"
        # commit BEFORE starting the range begin_ref_spec,end_rev_spec
        # Defined only if new_repo is True AND begin_rev_spec points to a
        # second-or-later changelist within our view.
        self.graft_change_num = None

        # sha1 string. Previous tip of Git history.
        # Set only if new_repo = False.
        self.last_commit      = None

    def __str__(self):
        return (( "b,e={begin_rev_spec},{end_rev_spec} new_repo={new_repo}"
                 + " graft={graft_change_num}, last_commit={last_commit}")
                 .format(begin_rev_spec  = self.begin_rev_spec,
                         end_rev_spec    = self.end_rev_spec,
                         new_repo        = self.new_repo,
                         graft_change_num= self.graft_change_num,
                         last_commit     = self.last_commit))

    def as_range_string(self):
        """Return 'begin,end'."""
        return "{begin},{end}".format(begin=self.begin_rev_spec,
                                      end=self.end_rev_spec)

    @classmethod
    def from_start_stop(cls,
                        ctx,
                        start_at="@1",
                        stop_at="#head"):
        """Factory: create and return a new RevRange object that has Perforce
           revision specifiers for begin and end.

        start_at: Accepts either Perforce revision specifier
                  OR a git sha1 for an existing git commit, which is then
                  mapped to a Perforce changelist number, and then we add 1 to
                  start copying ONE AFTER that sha1's corresponding Perforce
                  changelist.
        stop_at:  Usually "#head".
        """
        if (start_at.startswith("@")):
            return RevRange._new_repo_from_perforce_range(ctx,
                                                          start_at,
                                                          stop_at)
        else:
            return RevRange._existing_repo_after_commit(ctx,
                                                        start_at,
                                                        stop_at)

    @classmethod
    def _new_repo_from_perforce_range(cls,
                                      ctx,
                                      start_at, # @xxx Perforce rev specifier
                                      stop_at):
        """We're seeding a brand new repo that has no git commits yet.
        """
        result = RevRange()

        result.begin_rev_spec = start_at
        result.end_rev_spec   = stop_at
        result.new_repo       = True

        # Are there any PREVIOUS Perforce changelists before the requested
        # start of history? If so, then we'll need to graft our history onto
        # that previous point.
        if start_at != "@1":
            path = ctx.client_view_path()
            changes_result = ctx.p4.run("changes", "-m2", path + start_at)
            if 2 <= len(changes_result):
                #LOG.debug("graft calc")
                #LOG.debug(changes_result)
                # 'p4 changes' results are in reverse chronological order:
                # [0] most recent (start_at)
                # [1] is older (one before start_at)
                result.graft_change_num = int(changes_result[1]['change'])

        return result

    @classmethod
    def _existing_repo_after_commit(cls,
                                    ctx,
                                    start_at, # some git sha1, maybe partial
                                    stop_at):
        """We're adding to an existing git repo with an existing head.

        Find the Perforce submitted changelist that goes with start_at's Git
        commit sha1, then start at one after that.
        """
        last_commit = expand_sha1(start_at)
        last_changelist_number = (ctx.mirror
                                  .get_change_for_commit(last_commit,
                                                         ctx))
        if not last_changelist_number:
            raise RuntimeError((  "Invalid startAt={}: no commit sha1 with a"
                                + " corresponding Perforce changelist"
                                + " number.").format(start_at))

        result = RevRange()
        result.begin_rev_spec   = "@{}".format(1 + int(last_changelist_number))
        result.end_rev_spec     = stop_at
        result.new_repo         = False
        result.graft_change_num = None
        result.last_commit      = last_commit
        return result


# timer/counter names
OVERALL = "P4 to Git Overall"
SETUP = "Setup"
PRINT = "Print"
FSTAT = "Fstat"
SYNC = "Sync"
FAST_IMPORT = "Fast Import"
MIRROR = "Mirror"
MERGE = "Merge"
PACK = "Pack"


class P2G:
    """class to manage copying from Perforce to git"""
    def __init__(self, ctx):
        self.ctx = ctx
        self.fastimport = FastImport(self.ctx)
        self.fastimport.set_timezone(self.ctx.timezone)
        self.fastimport.set_project_root_path(self.ctx.contentlocalroot)
        self.perf = p4gf_profiler.TimerCounterSet()
        self.perf.add_timers([OVERALL,
                            (SETUP, OVERALL),
                            (PRINT, OVERALL),
                            (FSTAT, OVERALL),
                            (SYNC, OVERALL),
                            (FAST_IMPORT, OVERALL),
                            (MIRROR, OVERALL),
                            (MERGE, OVERALL),
                            (PACK, OVERALL)
                            ])

        self.rev_range      = None  # RevRange instance set in copy().
        self.graft_change   = None  #
        self.changes        = None  # dict['changelist'] ==> P4Changelist of what to copy()
        self.printed_revs   = None  # RevList produced by PrintHandler
        self.status_verbose = True
        self.progress       = ProgressReporter()

    def __str__(self):
        return "\n".join(["\n\nFast Import:\n",
                          str(self.fastimport),
                          "",
                          str(self.perf),
                          ""
                          ])

    def _setup(self, start_at, stop_at):
        """Set RevRange rev_range, figure out which changelists to copy."""
        self.rev_range = RevRange.from_start_stop(self.ctx, start_at, stop_at)
        LOG.debug("Revision range to copy to Git: {rr}"
                  .format(rr=self.rev_range))

        # get list of changes to import into git
        self.changes = P4Changelist.create_changelist_list_as_dict(
                            self.ctx.p4,
                            self._path_range())

        # If grafting, get that too.
        if self.rev_range.graft_change_num:
            # Ignore all depotFile elements, we just want the change/desc/time/user.
            self.graft_change = P4Changelist.create_using_describe(
                                    self.ctx.p4,
                                    self.rev_range.graft_change_num,
                                    "ignore_depot_files")
            self.graft_change.description += ('\n[grafted history before {start_at}]'
                                              .format(start_at=start_at))

    def _path_range(self):
        """Return the common path...@range string we use frequently.
        """
        return self.ctx.client_view_path() + self.rev_range.as_range_string()

    def _copy_print(self):
        """p4 print all revs and git-hash-object them into the git repo."""
        server_can_unexpand = self.ctx.p4.server_level > 32
        printhandler = PrintHandler(need_unexpand=not server_can_unexpand,
                                    tempdir=self.ctx.tempdir.name)
        self.ctx.p4.handler = printhandler
        args = ["-a"]
        if server_can_unexpand:
            args.append("-k")
        self.ctx.p4.run("print", args, self._path_range())
        printhandler.flush()
        printhandler.progress.progress_finish()

        # If also grafting, print all revs in existence at time of graft.
        if self.graft_change:
            args = []
            if server_can_unexpand:
                args.append("-k")
            path = self._graft_path()
            LOG.debug("Printing for grafted history: {}".format(path))
            self.ctx.p4.run("print", args, path)
            printhandler.flush()

            # If grafting, we just printed revs that refer to changelists
            # that have no P4Changelist counterpart in self.changes. Make
            # some skeletal versions now so that FstatHandler will have
            # someplace to hang its outputStat() P4File instances.
            for (_key, p4file) in printhandler.revs.revs:
                if not p4file.change in self.changes:
                    cl = P4Changelist()
                    cl.change = p4file.change
                    self.changes[p4file.change] = cl

        self.ctx.p4.handler = None
        self.printed_revs = printhandler.revs

    def _fstat(self):
        """run fstat to find deleted revs and get client paths"""
        # TODO for 12.2 print will also report deleted revs so between
        # that and using MapApi to get client paths, we won't need this fstat
        self.ctx.p4.handler = FstatHandler(self.printed_revs, self.changes)
        fstat_cols = "-T" + ",".join(P4File.fstat_cols())
        self.ctx.p4.run("fstat", "-Of", fstat_cols, self._path_range())

        if self.graft_change:
            # Also run 'p4 fstat //<view>/...@change' for the graft
            # change to catch all files as of @change, not just
            # revs changed between begin and end of _path_range().
            self.ctx.p4.run("fstat", fstat_cols, self._graft_path())

        self.ctx.p4.handler = None

        self._collapse_to_graft_change()
        self._add_graft_to_changes()

        # don't need this any more
        self.printed_revs = None

        sorted_changes = [str(y) for y in sorted([int(x) for x in self.changes.keys()])]

        LOG.debug("\n".join([str(self.changes[ch]) for ch in sorted_changes]))
        return sorted_changes

    def _sync(self, sorted_changes):
        """fake sync of last change to make life easier at push time"""
        self.ctx.p4.handler = SyncHandler()
        lastchange = self.changes[sorted_changes[-1]]
        self.ctx.p4.run("sync", "-kf",
                self.ctx.client_view_path() + "@" + str(lastchange.change))
        self.ctx.p4.handler = None

    def _fast_import(self, sorted_changes, last_commit):
        """build fast-import script from changes, then run fast-import"""
        self.progress.progress_init_determinate(len(sorted_changes))
        for changenum in sorted_changes:
            change = self.changes[changenum]
            self.progress.progress_increment("Copying changelists...")
            self.ctx.heartbeat()

            # create commit and trees
            self.fastimport.add_commit(change, last_commit)

            last_commit = change.change

        # run git-fast-import and get list of marks
        marks = self.fastimport.run_fast_import()

        # done with these
        self.changes = None
        return marks

    def _mirror(self, marks):
        """build up list of p4 objects to mirror git repo in perforce
        then submit them
        """
        self.ctx.mirror.add_commits(marks)
        self.ctx.mirror.add_objects_to_p4(self.ctx)
        LOG.getChild("time").debug("\n\nGit Mirror:\n" + str(self.ctx.mirror))
        self.ctx.mirror = GitMirror(self.ctx.config.view_name)

        last_commit = marks[len(marks) - 1]
        LOG.debug("Last commit created: " + last_commit)

    # pylint: disable=R0201
    # R0201 Method could be a function
    def _pack(self):
        """run 'git gc' to pack up the blobs

        aside from any possible performance benefit, this prevents warnings
        from git about "unreachable loose objects"
        """
        p4gf_util.popen_no_throw(["git", "gc"])

    def _collapse_to_graft_change(self):
        """Move all of the files from pre-graft changelists into the graft
        changelist. Remove all pre-graft changelists.

        NOP if not grafting.

        'p4 print //client/...@100' does indeed print all the files that
        exist @100, but the tag dict that goes with each file includes the
        changelist in which that file was last added/edited, not 100. So
        this function gathers up all the file revs with change=1..99 and
        sticks them under change 100's file list.
        """
        if (not self.graft_change):
            return
        graft_num_int = int(self.graft_change.change)
        LOG.debug("_collapse_to_graft_change() graft_num_int={}".format(graft_num_int))

        # Delete all P4Changelist elements from self.changes where they
        # refer to a change that will be collapsed into the graft change,
        # including the graft change itself.
        del_keys = []
        for p4changelist in self.changes.values():
            if graft_num_int < int(p4changelist.change):
                LOG.debug("_collapse_to_graft_change() skipping {}".format(p4changelist.change))
                continue

            LOG.debug("_collapse_to_graft_change() deleting {}".format(p4changelist.change))
            del_keys.append(p4changelist.change)
        for key in del_keys:
            del self.changes[key]

        # Associate with the graft change all printed P4File results from
        # graft-change or older
        for (_key, p4file) in self.printed_revs.revs:
            if graft_num_int < int(p4file.change):
                LOG.debug("_collapse_to_graft_change() skipping post-graft {}".format(p4file))
                continue

            old = self.graft_change.file_from_depot_path(p4file.depot_path)
            # If print picked up multiple revs, keep the newest.
            if (not old) or (int(old.change) < int(p4file.change)):
                p4file.change = self.graft_change.change
                self.graft_change.files.append(p4file)
                LOG.debug("_collapse_to_graft_change() keeping {}".format(p4file))
            else:
                LOG.debug("_collapse_to_graft_change() skipping, had newer  {}".format(p4file))

    def _add_graft_to_changes(self):
        """Add the graft changelist to our list of changes:
        It will be copied over like any other change.

        NOP if not grafting.
        """
        if (not self.graft_change):
            return
        self.changes[self.graft_change.change] = self.graft_change

    def _graft_path(self):
        """If grafting, return '//<client>/...@N' where N is the graft
        changelist number.

        If not grafting, return None.
        """
        if (not self.graft_change):
            return
        return "{path}@{change}".format(
                        path = self.ctx.client_view_path(),
                        change = self.graft_change.change)

    def copy(self, start_at, stop_at):
        """copy a set of changelists from perforce into git"""

        with self.perf.timer[OVERALL]:
            with self.perf.timer[SETUP]:
                self._setup(start_at, stop_at)

                if not len(self.changes):
                    LOG.debug("No new changes found to copy")
                    return

                last_commit = self.rev_range.last_commit

            with self.perf.timer[PRINT]:
                self._copy_print()

            with self.perf.timer[FSTAT]:
                sorted_changes = self._fstat()

            with self.perf.timer[SYNC]:
                self._sync(sorted_changes)

            with self.perf.timer[FAST_IMPORT]:
                marks = self._fast_import(sorted_changes, last_commit)
                sorted_changes = None

            with self.perf.timer[MIRROR]:
                self._mirror(marks)

            with self.perf.timer[MERGE]:
                # merge temporary branch into master, then delete it
                self.fastimport.merge()

            with self.perf.timer[PACK]:
                self._pack()

        LOG.getChild("time").debug("\n" + str(self))


def copy_p4_changes_to_git(ctx, start_at, stop_at):
    """copy a set of changelists from perforce into git"""

    p2g = P2G(ctx)
    p2g.copy(start_at, stop_at)


def get_options():
    """arg parsing for test mode"""
    result = namedtuple('Result', ['start_at', 'stop_at'])("@1", "#head")
    #result = Result("1", "#head")

    for i in range(1, len(sys.argv)):
        arg = sys.argv[i]
        if arg.startswith("--startAt="):
            # pylint: disable=W0212
            # W0212:  Access to a protected member %s of a client class
            # Yes, because namedtuple._replace() is useful and should
            # not be protected.
            result = result._replace(start_at=arg[10:])
        elif arg.startswith("--stopAt="):
            # pylint: disable=W0212
            # W0212:  Access to a protected member %s of a client class
            # Yes, because namedtuple._replace() is useful and should
            # not be protected.
            result = result._replace(stop_at=arg[9:])
    return result
