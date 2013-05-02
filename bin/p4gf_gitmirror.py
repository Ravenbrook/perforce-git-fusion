#! /usr/bin/env python3.2
"""GitMirror class"""

import os
import re
import zlib
from subprocess import Popen, PIPE
import p4gf_log
import p4gf_p4msgid
import p4gf_object_type
import p4gf_profiler
from   p4gf_progress_reporter import ProgressReporter

from P4 import OutputHandler

LOG = p4gf_log.for_module()

# pylint: disable=C0103
# C0103 Invalid name
# These names are imposed by P4Python


class FilterAddFstatHandler(OutputHandler):
    """OutputHandler for p4 fstat, builds list of files that don't already exist,
    as well as gathering the 'views' attribute values for existing files.
    """
    def __init__(self, view_name):
        OutputHandler.__init__(self)
        self.view_name = view_name
        self.files = []
        self.existing = []

    def outputStat(self, h):
        """grab depotFile and associate one or more views with it"""
        views = h.get("attr-views", '')
        parts = views.split('#')
        if self.view_name not in parts:
            parts.append(self.view_name)
            views = '#'.join(parts)
            self.existing.append((h["depotFile"], views))
        return OutputHandler.HANDLED

    def outputMessage(self, m):
        """outputMessage call expected for any files not already added;
        otherwise indicates an error
        """
        if (m.msgid == p4gf_p4msgid.MsgDm_ExFILE):
            self.files.append(m.dict['argc'])
        return OutputHandler.REPORT
# pylint: enable=C0103


def mirror_path(root, sha1, objtype):
    """create path for a git mirror object in perforce

    root: depot path to mirrored objects, e.g. //.git-fusion/
    sha1: SHA1 of mirrored object
    objtype: objtype of mirrored object (commit, tag, tree)

    path is constructed by:
        1) adding 'objects/' to root
        2) using part of SHA1 to create folder hierarchy under objects
        3) using remainder of SHA1 to create base filename
        4) appending object objtype to filename

    ### See also duplicate implementation p4gf_object_type._sha1_to_object_type_list()
    """
    return "{0}objects/{1}/{2}/{3}-{4}".format(root,
                                               sha1[:2],
                                               sha1[2:4],
                                               sha1[4:],
                                               objtype)


class GitObject:
    """a git object from .git/objects which gets mirrored in //.git-fusion

    one of: commit, tree, tag
    """

    def __init__(self, objtype, sha1, p4changelists=None):
        """Init a GitObject

            objtype is one of:
                commit, tree, tag

            p4changelists is a list of 2-tuples: 
                [ (changelist number, view name), ... ]:
        """
        self.type = objtype
        self.sha1 = sha1
        self.p4changelists = p4changelists

    def git_p4_client_path(self, ctx):
        """path to object in Perforce mirror of .git folder

        ctx.gitlocalroot is local root of git-fusion in Perforce
        """
        path = mirror_path(ctx.gitlocalroot, self.sha1, self.type)
        if self.type == "commit":
            if len(self.p4changelists) != 1:
                raise RuntimeError(
                       "Bug: commit objects can only produce single"
                     + " path if given a single changelist and view name"
                     + " {}".format(self))
            cl = self.p4changelists[0]
            path = ("{path}-{changenum}-{viewname}"
                    .format( path      = path
                           , changenum = cl[0]
                           , viewname  = cl[1]))
        return path

    def __str__(self):
        return "{0} {1} {2}".format(self.type, self.sha1, self.p4changelists)

    def __repr__(self):
        return str(self)


class GitObjectList:
    """a list of GitObjects"""

    def __init__(self):
        self.objects = {}
        self.counts = {'commit': 0, 'tree': 0, 'tag': 0}

    def add_object(self, obj_to_add):
        """skip over duplicate objects (e.g. tree shared by commits)"""
        key = obj_to_add.type + obj_to_add.sha1
        if not key in self.objects:
            self.objects[key] = obj_to_add
            self.counts[obj_to_add.type] += 1

    def __str__(self):
        items = []
        for key, value in self.counts.items():
            items.append("{0} {1}s".format(value, key))
        return "\n".join(items)

    def __repr__(self):
        items = []
        for obj in self.objects.values():
            items.append(repr(obj))
        items.append(str(self))
        return "\n".join(items)

# timer/counter names
OVERALL = "GitMirror Overall"

BUILD = "Build"
CAT_FILE = "cat-file"
LS_TREE = "ls-tree"
LS_TREE_PROCESS = "ls-tree process"
DIFF_TREE = "diff-tree"
DIFF_TREE_PROCESS = "diff-tree process"
CAT_FILE_COUNT = "cat-file files"
CAT_FILE_SIZE = "cat-file size"

ADD_SUBMIT = "Add/Submit"
EXTRACT_OBJECTS = "extract objects"
P4_FSTAT = "p4 fstat"
P4_ADD = "p4 add"
P4_SUBMIT = "p4 submit"


def build_view_mapping(existing_files):
    """Build a mapping of views and the files that map to them so we can
    minimize the number of times we invoke p4 attribute. The first element
    in the returned tuple is the list of all files in the given dict. The
    second element is a mapping of the view names (in sorted order) to the
    list of associated files.

    Arguments:
        existing_files: list of tuples of depot paths and associated views.

    Returns:
        Tuple of depot paths list and mapping of views to file lists.
    """
    existing_names = []
    view_files = dict()
    for ef in existing_files:
        existing_names.append(ef[0])
        views = ef[1]
        if views in view_files:
            view_files[views].append(ef[0])
        else:
            view_files[views] = [ef[0]]
    view_keys = list(view_files.keys())
    # Sort the view names over the set of combinations rather than the set of
    # all files, which would likely be a more expensive operation.
    for views in view_keys:
        view_parts = views.split('#')
        if len(view_parts) > 1:
            view_parts.sort()
            news = '#'.join(view_parts)
            if news != views:
                view_keys.remove(views)
                files = view_files[views]
                del view_files[views]
                if news in view_files:
                    view_files[news].extend(files)
                else:
                    view_files[news] = files
    return (existing_names, view_files)


def edit_objects_with_views(ctx, existing_files):
    """For the list of existing files, open them for edit and update
    their 'views' attribute to reflect the newly associated view.
    """
    existing_names, view_mapping = build_view_mapping(existing_files)
    bite_size = 1000
    while len(existing_names):
        bite = existing_names[:bite_size]
        existing_names = existing_names[bite_size:]
        ctx.p4gf.run("edit", bite)
    for views, names in view_mapping.items():
        while len(names):
            bite = names[:bite_size]
            names = names[bite_size:]
            ctx.p4gf.run("attribute", "-p", "-n", "views", "-v", views, bite)


class GitMirror:
    """handle git things that get mirrored in perforce"""

    def __init__(self, view_name):
        self.git_objects = GitObjectList()
        self.perf = p4gf_profiler.TimerCounterSet()
        self.perf.add_timers([OVERALL,
                             (BUILD, OVERALL),
                             (CAT_FILE, BUILD),
                             (LS_TREE, BUILD),
                             (LS_TREE_PROCESS, BUILD),
                             (DIFF_TREE, BUILD),
                             (DIFF_TREE_PROCESS, BUILD),
                             (ADD_SUBMIT, OVERALL),
                             (EXTRACT_OBJECTS, ADD_SUBMIT),
                             (P4_FSTAT, ADD_SUBMIT),
                             (P4_ADD, ADD_SUBMIT),
                             (P4_SUBMIT, ADD_SUBMIT),
                             ])
        self.perf.add_counters([(CAT_FILE_COUNT, "files"),
                                (CAT_FILE_SIZE, "bytes")])
        self.progress = ProgressReporter()
        self.view_name = view_name

    @staticmethod
    def get_change_for_commit(commit, ctx):
        """Given a commit sha1, find the corresponding perforce change.
        """
        object_type = p4gf_object_type.sha1_to_object_type(
                              sha1           = commit
                            , view_name      = ctx.config.view_name
                            , p4             = ctx.p4gf
                            , raise_on_error = False)
        if not object_type:
            return None
        return object_type.view_name_to_changelist(ctx.config.view_name)

    def add_commits(self, marks):
        """build list of commit and tree objects for a set of changelists

        marks: list of commit marks output by git-fast-import
               formatted as: :changenum sha1
        """

        with self.perf.timer[OVERALL]:
            with self.perf.timer[BUILD]:
                last_top_tree = None
                for mark in marks:
    
                    #parse perforce change number and SHA1 from marks
                    parts = mark.split(' ')
                    change_num = parts[0][1:]
                    sha1 = parts[1].strip()
    
                    # add commit object
                    self.git_objects.add_object(
                        GitObject( "commit"
                                 , sha1
                                 , [(change_num, self.view_name)]
                                 ))
    
                    # add all trees referenced by the commit
                    if last_top_tree:
                        last_top_tree = self.__get_delta_trees(last_top_tree, sha1)
                    else:
                        last_top_tree = self.__get_snapshot_trees(sha1)

    def add_objects_with_views(self, ctx, add_files):
        """Add the list of files to the object cache in the depot and
        return the number of files not added.
        """
        added_files = []
        files_not_added = 0
        treecount = 0
        commitcount = 0
        # Add new files to the object cache.
        bite_size = 1000
        while len(add_files):
            bite = add_files[:bite_size]
            add_files = add_files[bite_size:]
            result = ctx.p4gf.run("add", "-t", "binary", bite)
            for m in [m for m in ctx.p4gf.messages
                      if (m.msgid != p4gf_p4msgid.MsgDm_OpenUpToDate or
                          m.dict['action'] != 'add')]:
                files_not_added += 1
                LOG.debug(str(m))

            for r in [r for r in result if isinstance(r, dict)]:
                if r["action"] != 'add':
                    # file already exists in depot, perhaps?
                    files_not_added += 1
                    LOG.debug(r)
                else:
                    added_files.append(r["depotFile"])
                    if r["depotFile"].endswith("-tree"):
                        treecount += 1
                    else:
                        commitcount += 1
        LOG.debug("Added {} commits and {} trees".format(commitcount, treecount))
        # Set the 'views' attribute on the opened files.
        while len(added_files):
            bite = added_files[:bite_size]
            added_files = added_files[bite_size:]
            ctx.p4gf.run("attribute", "-p", "-n", "views", "-v", self.view_name, bite)
        return files_not_added

    def add_objects_to_p4(self, ctx):
        """actually run p4 add, submit to create mirror files in .git-fusion"""

        with self.perf.timer[OVERALL]:
            # Revert any opened files left over from a failed mirror operation.
            opened = ctx.p4gf.run('opened')
            if opened:
                ctx.p4gf.run('revert', '//{}/...'.format(ctx.config.p4client_gf))
            with self.perf.timer[ADD_SUBMIT]:
                LOG.debug("adding {0} commits and {1} trees to .git-fusion...".
                          format(self.git_objects.counts['commit'],
                                 self.git_objects.counts['tree']))

                # build list of objects to add, extracting them from git
                self.progress.progress_init_determinate(len(self.git_objects.objects))
                add_files = [self.__add_object_to_p4(ctx, go)
                              for go in self.git_objects.objects.values()]

                # filter out any files that have already been added
                # only do this if the number of files is large enough to justify
                # the cost of the fstat
                existing_files = None
                with self.perf.timer[P4_FSTAT]:
                    # Need to use fstat to get the 'views' attribute for existing
                    # files, which we can't know until we use fstat to find out.
                    bite_size = 1000
                    LOG.debug("using fstat to optimize add")
                    original_count = len(add_files)
                    ctx.p4gf.handler = FilterAddFstatHandler(self.view_name)
                    # spoon-feed p4 to avoid blowing out memory
                    while len(add_files):
                        bite = add_files[:bite_size]
                        add_files = add_files[bite_size:]
                        # Try to get only the information we really need.
                        ctx.p4gf.run("fstat", "-Oa", "-T", "depotFile, attr-views", bite)
                    add_files = ctx.p4gf.handler.files
                    existing_files = ctx.p4gf.handler.existing
                    ctx.p4gf.handler = None
                    LOG.debug("{} files removed from add list"
                              .format(original_count - len(add_files)))

                files_to_add = len(add_files) + len(existing_files)
                if files_to_add == 0:
                    return

                with self.perf.timer[P4_ADD]:
                    files_not_added = self.add_objects_with_views(ctx, add_files)
                    edit_objects_with_views(ctx, existing_files)

                with self.perf.timer[P4_SUBMIT]:
                    if files_not_added < files_to_add:
                        desc = 'Git Fusion {view} copied to git'.format(
                                view=ctx.config.view_name)
                        self.progress.status("Submitting new Git objects to Perforce...")
                        ctx.p4gf.run("submit", "-d", desc)
                    else:
                        LOG.debug("ignoring empty change list...")

    def __str__(self):
        return "\n".join([str(self.git_objects),
                          str(self.perf)
                          ])

    def __repr__(self):
        return "\n".join([repr(self.git_objects),
                          str(self.perf)
                          ])

    # pylint: disable=R0201, W1401
    # R0201 Method could be a function
    # I agree, this _could_ be a function, does not need self. But when I
    # blindly promote this to a module-level function, things break and I
    # cannot explain why.
    # W1401 Unescaped backslash
    # We want that null for the header, so we're keeping the backslash. 
    def __add_object_to_p4(self, ctx, go):
        """add a commit or tree to the git-fusion perforce client workspace

        return the path of the client workspace file suitable for use with
        p4 add
        """
        self.progress.progress_increment("Adding new Git objects to Perforce...")
        ctx.heartbeat()

        # get client path for .git-fusion file
        dst = go.git_p4_client_path(ctx)

        # A tree is likely to already exist, in which case we don't need
        # or want to try to recreate it.  We'll just use the existing one.
        if os.path.exists(dst):
            LOG.debug("reusing existing object: " + dst)
            return dst

        with self.perf.timer[EXTRACT_OBJECTS]:

            # make sure dir exists
            dstdir = os.path.dirname(dst)
            if not os.path.exists(dstdir):
                os.makedirs(dstdir)

            # get contents of commit or tree; can't just copy it because it's
            # probably in a packfile and we don't know which one.  And there's
            # no way to have git give us the compressed commit directly, so we
            # need to recompress it
            p = Popen(['git', 'cat-file', go.type, go.sha1], stdout=PIPE)
            po = p.communicate()[0]
            header = go.type + " " + str(len(po)) + '\0'
            deflated = zlib.compress(header.encode() + po)

            # write it into our p4 client workspace for adding.
            LOG.debug("adding new object: " + dst)
            f = open(dst, "wb")
            f.write(deflated)
            f.close()

            return dst

    def __get_snapshot_trees(self, commit):
        """get all tree objects for a given commit
            commit: SHA1 of commit

        each tree is added to the list to be mirrored

        return the SHA1 of the commit's tree
        """

        top_tree = self.__get_commit_tree(commit)
        with self.perf.timer[LS_TREE]:
            p = Popen(['git', 'ls-tree', '-rt', top_tree], stdout=PIPE)
            po = p.communicate()[0].decode()
        with self.perf.timer[LS_TREE_PROCESS]:
            # line is: mode SP type SP sha TAB path
            # we only want the sha from lines with type "tree"
            pattern = re.compile("^[0-7]{6} tree ([0-9a-fA-F]{40})\t.*")
            # yes, we're doing nothing with the result of this list comprehension
            # pylint: disable=W0106
            [self.git_objects.add_object(GitObject("tree", m.group(1)))
                                         for line in po.splitlines()
                                            for m in [pattern.match(line)]
                                                if m]
            # pylint: enable=W0106
        return top_tree

    def __get_delta_trees(self, top_tree1, commit2):
        """get all tree objects new in one commit vs another commit
            topTree1: SHA1 of first commit's tree
            commit2: SHA1 of second commit

        each tree is added to the list to be mirrored

        return the SHA1 of commit2's tree
        """
        top_tree2 = self.__get_commit_tree(commit2)
        with self.perf.timer[DIFF_TREE]:
            p = Popen(['git', 'diff-tree', '-t', top_tree1, top_tree2], stdout=PIPE)
            po = p.communicate()[0].decode()
        with self.perf.timer[DIFF_TREE_PROCESS]:
            # line is: :mode1 SP mode2 SP sha1 SP sha2 SP action TAB path
            # we want sha2 from lines where mode2 indicates a dir
            pattern = re.compile(
                "^:[0-7]{6} ([0-7]{2})[0-7]{4} [0-9a-fA-F]{40} ([0-9a-fA-F]{40}) .*")
            # yes, we're doing nothing with the result of this list comprehension
            # pylint: disable=W0106
            [self.git_objects.add_object(GitObject("tree", m.group(2)))
                             for line in po.splitlines()
                                for m in [pattern.match(line)]
                                    if m and m.group(1) == "04"]
            # pylint: enable=W0106
        return top_tree2

    def __get_commit_tree(self, commit):
        """get the one and only tree at the top of commit

            commit: SHA1 of the commit

        add the tree object to the list of objects to be mirrored
        and return its SHA1
        """

        with self.perf.timer[CAT_FILE]:
            self.perf.counter[CAT_FILE_COUNT] += 1
            p = Popen(['git', 'cat-file', 'commit', commit], stdout=PIPE)
            po = p.communicate()[0].decode()
            self.perf.counter[CAT_FILE_SIZE] += len(po)
            for line in iter(po.splitlines()):
                if not line.startswith("tree"):
                    continue
                # line is: tree sha
                parts = line.strip().split(' ')
                sha1 = parts[1]
                self.git_objects.add_object(GitObject("tree", sha1))
                return sha1
