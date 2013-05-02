#! /usr/bin/env python3.2
"""FastImport class"""

import logging
import re
from subprocess import Popen, check_call, check_output, CalledProcessError
import tempfile
import p4gf_const
import p4gf_profiler
import p4gf_usermap

LOG = logging.getLogger(__name__)

OVERALL = "FastImport Overall"
BUILD = "Build"
RUN = "Run"
MERGE = "Merge"
SCRIPT_LINES = "Script length"
SCRIPT_BYTES = "Script size"

class FastImport:
    """Create a git-fast-import script and use it to import from Perforce.

    Steps to use FastImport:

    1) Call set_timezone() with Perforce server timezone.
    2) Call set_project_root_path() with local root path of view.
    3) For each Perforce changelist to import:
            Call add_commit() with changelist and files.
            This adds everything necessary for one commit to the fast-import
            script.  The mark for the commit will be the change number.
    4) Call run_fast_import() to run git-fast-import with the script produced
       by steps 1-4.
    5) Call merge() to run git-merge and then delete the temporary branch.
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self.script = tempfile.NamedTemporaryFile(dir=self.ctx.tempdir.name)
        self.timezone = None
        self.project_root_path_length = -1
        self.branchname = p4gf_const.P4GF_BRANCH_TEMP
        self.perf = p4gf_profiler.TimerCounterSet()
        self.perf.add_timers([OVERALL,
                             (BUILD, OVERALL),
                             (RUN, OVERALL),
                             (MERGE, OVERALL),
                             ])
        self.perf.add_counters([(SCRIPT_LINES, "lines"),
                                (SCRIPT_BYTES, "bytes")
                                ])
        self.username_map = dict()
        self.usermap = p4gf_usermap.UserMap(ctx.p4gf)

    def set_timezone(self, tz):
        """Set timezone of perforce server."""
        self.timezone = tz

    def set_project_root_path(self, path):
        """Set local root path of view."""
        self.project_root_path_length = len(path)

    def __append(self, data):
        """append data to script"""
        if type(data) == str:
            data = data.encode()
        self.script.write(data)
        self.perf.counter[SCRIPT_BYTES] += len(data)
        self.perf.counter[SCRIPT_LINES] += data.count(b'\n')

    def __add_data(self, string):
        """append a string to fast-import script, git style"""
        encoded = string.encode()
        header = "data {}\n".format(len(encoded))
        self.__append(header.encode() + encoded)

    def __relative_path(self, p4file):
        """return local path of p4file, relative to view root"""
        assert len(p4file.client_path) > self.project_root_path_length
        return p4file.client_path[self.project_root_path_length:]

    def __add_files(self, snapshot):
        """write files in snapshot to fast-import script"""
        for p4file in snapshot:
            path = self.__relative_path(p4file)
            if p4file.is_delete():
                self.__append("D {0}\n".format(path))
            else:
                if p4file.sha1 == "":
                    LOG.debug("skipping missing revision {}#{}".format(path, p4file.revision))
                    continue
                if p4file.is_x_type():
                    mode = "100755"
                elif p4file.is_symlink():
                    mode = "120000"
                else:
                    mode = "100644"
                self.__append("M {0} {1} {2}\n".
                              format(mode, p4file.sha1, path))

    def __email_for_user(self, username):
        """get email address for a user"""
        user_3tuple = self.usermap.lookup_by_p4user(username)
        if not user_3tuple:
            return "Unknown Perforce User <{}>".format(username)
        return "<{0}>".format(user_3tuple[p4gf_usermap.TUPLE_INDEX_EMAIL])

    def __full_name_for_user(self, username):
        """get human's first/last name for a user"""

        # First check our cache of previous hits.
        if username in self.username_map:
            return self.username_map[username]

        # Fall back to p4gf_usermap, p4 users.
        user_3tuple = self.usermap.lookup_by_p4user(username)
        if user_3tuple:
            user = p4gf_usermap.tuple_to_P4User(user_3tuple)
        else:
            user = None
        fullname = ''
        if user:
            # remove extraneous whitespace for consistency with Git
            fullname = ' '.join(user.full_name.split())
        self.username_map[username] = fullname
        return fullname

    def add_commit(self, cl, last):
        """Add a commit to the fast-import script.

        Arguments:
        cl -- P4Changelist to turn into a commit
        last -- mark or SHA1 of commit this commit will be based on
        cl.files -- [] of P4File containing files in changelist
        """
        with self.perf.timer[OVERALL]:
            with self.perf.timer[BUILD]:
                self.__append("commit refs/heads/{0}\n".format(self.branchname))
                self.__append("mark : {0}\n".format(cl.change))
                impidx = cl.description.find(p4gf_const.P4GF_IMPORT_HEADER)
                committer_added = False
                if impidx > -1:
                    # extract the original author and committer data;
                    # note that the order matters with fast-import
                    suffix = cl.description[impidx:]
                    for key in ('author', 'committer'):
                        regex = re.compile(key.capitalize() + r': (.+) (<.+>) (\d+) (.+)')
                        match = regex.search(suffix)
                        if match:
                            self.__append("{key} {fullname} {email} {time} {timezone}\n".
                                          format(key=key,
                                                 fullname=match.group(1),
                                                 email=match.group(2),
                                                 time=match.group(3),
                                                 timezone=match.group(4)))
                            committer_added = True
                    # prune Git Fusion noise added in p4gf_copy_to_p4
                    # (including the newline added between the parts)
                    desc = cl.description[0:impidx-1]

                # Convoluted logic gates but avoids duplicating code. The point
                # is that we add the best possible committer data _before_
                # adding the description.
                if not committer_added:
                    if impidx > -1:
                        # old change description that lacked detailed author info,
                        # deserves a warning, but otherwise push onward even if the
                        # commit checksums will likely differ from the originals
                        LOG.warn('commit description did not match committer regex: @{} => {}'.
                                 format(cl.change, suffix))
                    self.__append("committer {fullname} {email} {time} {timezone}\n".
                                  format(fullname=self.__full_name_for_user(cl.user),
                                         email=self.__email_for_user(cl.user),
                                         time=cl.time,
                                         timezone=self.timezone))
                    desc = cl.description
                self.__add_data(desc)

                #if this is not the initial commit, say what it's based on
                #otherwise start with a clean slate
                if last:
                    #last is either SHA1 of an existing commit or mark of a commit
                    #created earlier in this import operation.  Assume a length of
                    #40 indicates the former and mark ids will always be shorter.
                    if len(last) == 40:
                        self.__append("from {0}\n".format(last))
                    else:
                        self.__append("from :{0}\n".format(last))
                else:
                    self.__append("deleteall\n")
                self.__add_files(cl.files)

    def run_fast_import(self):
        """Run git-fast-import to create the git commits.

        Returns: a list of commits.  Each line is formatted as
            a change number followed by the SHA1 of the commit.

        The returned list is also written to a file called marks.
        """
        with self.perf.timer[OVERALL]:
            with self.perf.timer[RUN]:
                LOG.debug("running git fast-import")
                # tell git-fast-import to export marks to a temp file
                self.script.flush()
                self.script.seek(0)
                marks_file = tempfile.NamedTemporaryFile(dir=self.ctx.tempdir.name)
                p = Popen(['git', 'fast-import', '--quiet', '--export-marks=' + marks_file.name],
                        stdin=self.script)
                # pylint: disable=E1101
                # Instance of '' has no '' member
                p.wait()
                if p.returncode:
                    raise CalledProcessError(p.returncode, "git fast-import")

                #read the exported marks from file and return result
                with open(marks_file.name, "r") as marksfile:
                    marks = marksfile.readlines()

                return marks

    def merge(self):
        """Run git-merge to merge the imported commits."""
        with self.perf.timer[OVERALL]:
            with self.perf.timer[MERGE]:
                check_output(['git', 'status'])
                LOG.debug("git merge --quiet --ff-only {0}".format(self.branchname))
                check_call(['git', 'merge', '--quiet', '--ff-only', self.branchname])

    def __repr__(self):
        return "\n".join([repr(self.ctx),
                          "timezone                : " + self.timezone,
                          "project_root_path_length: " + str(self.project_root_path_length),
                          "branchname              : " + self.branchname,
                          str(self.perf)
                          ])

    def __str__(self):
        return "\n".join([str(self.ctx),
                          "timezone                : " + self.timezone,
                          "project_root_path_length: " + str(self.project_root_path_length),
                          "branchname              : " + self.branchname,
                          str(self.perf),
                          ])
