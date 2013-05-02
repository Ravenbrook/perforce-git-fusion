#! /usr/bin/env python3.2
"""copy_git_changes_to_p4"""

import os
import shutil
import stat
import logging
import time
import traceback

import P4

from   p4gf_create_p4 import connect_p4
from   p4gf_g2p_conflict_checker import G2PConflictChecker
import p4gf_const
import p4gf_fastexport
import p4gf_p4filetype
import p4gf_p4msg
import p4gf_p4msgid
import p4gf_usermap
import p4gf_protect
import p4gf_path
import p4gf_profiler
from   p4gf_progress_reporter import ProgressReporter
import p4gf_util

LOG = logging.getLogger(__name__)


def p4type_from_mode(mode):
    """Map Git file mode to Perforce filetype, wherein symbolic links
    get type 'symlink' and executable files get '+x'. Everything else
    is left uninterpreted so as to have Perforce guess the file type.

    Args:
        mode: String containing octal mode, as reported by fast-export.

    Returns:
        Either 'symlink', '+x', or None if the type cannot be determined.
    """
    if mode == "120000":
        return "symlink"
    if int(mode, 8) & 0o100:
        return "+x"
    return None


def changelist_from_submit_result(r):
    """"Search for 'submittedChange'"""
    for d in r:
        if 'submittedChange' in d:
            return d['submittedChange']
    return None


def change_description(commit, pusher, author):
    """Construct a changelist description from a git commit.

    Keyword arguments:
    commit  -- commit data from Git
    pusher  -- name of the authenticated pusher
    author  -- name of the user who made the commit
    """
    parts = [commit['data']]
    # Avoid adding anything between the data and the 'Imported' line below,
    # otherwise be sure to update the add_commit() code in p4gf_fastimport
    # that strips away this audit fluff when re-cloning the changes.
    parts.append(p4gf_const.P4GF_IMPORT_HEADER)
    # Collect all of the author/committer data so we can faithfully
    # reproduce the original commit in p4gf_fastimport, if needed.
    for key in ('author', 'committer'):
        datum = commit[key]
        parts.append(" {0}: {1} {2} {3} {4}".format(
                key.capitalize(), datum['user'], datum['email'], datum['date'], datum['timezone']))
    if pusher != author:
        parts.append(" Pusher: {}".format(pusher))
    parts.append(" sha1: {}".format(commit['sha1']))
    return "\n".join(parts)

def p4_submit(p4, desc, author, author_date):
    """This is the function called once for each git commit as it is
    submitted to Perforce. If you need to customize the submit or change
    the description, here is where you can do so safely without
    affecting the rest of Git Fusion.

    Since p4 submit does not allow submitting on behalf of another user
    we must first submit as git-fusion-user and then edit the resulting
    changelist to set the 'User' field to the actual author of the change.

    Implements CALL#3507045/job055710 "Allow for a user-controlled
    submit step."

    author_date can be either integer "seconds since the epoch" or a
    Perforce-formatted timestamp string YYYY/MM/DD hh:mm:ss. Probably needs to
    be in the server's timezone.
    """
    # Avoid fetch_change() and run_submit() since that exposes us to the
    # issue of filenames with double-quotes in them (see job015259).
    r = p4.run('submit', '-d', desc)
    changenum = changelist_from_submit_result(r)
    LOG.debug("Submitted change: {}".format(r))
    change = p4.fetch_change(changenum)
    change['User'] = author
    change['Date'] = author_date   # both number or string work here
    p4.save_change(change, '-f')
    LOG.debug("Changing change owner to: {}".format(author))
    return changenum


def contains_desc(desc, changelist_array):
    """Does ANY changelist in the given array have a description that
    contains the requested desc?
    """
    log = logging.getLogger("contains_desc")
    for changelist in changelist_array:
        found = desc in changelist['desc']
        log.debug("found={f} desc={d}".format(f=found, d=changelist['desc']))
        if found:
            return True
    return False


def mark_to_commit_changelist(mark):
    """Convert a git-fast-export mark "x" to a two-element array
    [git commit sha1, p4 changelist number].

    Mark format is :p4<sp>sha1

    :1234 8d0d7720ae2eb4f0ed2f4fddc3b2a56abbf8c0e5
    """

    r = mark[1:].split(' ')
    LOG.debug("mark_to_commit_changelist mark={mark} r={r}".format(mark=mark, r=r))
    return [ r[1], r[0] ]


def check_valid_filename(name):
    """Test the given name for illegal characters, returning None if okay,
    otherwise an error message. Illegal characters and sequences include:
    [...]
    """
    if '...' in name:
        return "bad filename: {}".format(name)
    return None


def escape_path(path):
    """escape special characters before sending to p4d"""
    return path.replace('%','%25').replace('#', '%23').replace('@', '%40').replace('*', '%2A')

# p4d treats many failures to open a file for {add, edit, delete, others}
# not as an E_FAILED error, but as an E_INFO "oh by the way I totally failed
# to do what you want.
#
MSGID_CANNOT_OPEN = [ p4gf_p4msgid.MsgDm_LockSuccess
                    , p4gf_p4msgid.MsgDm_LockAlready
                    , p4gf_p4msgid.MsgDm_LockAlreadyOther
                    , p4gf_p4msgid.MsgDm_LockNoPermission
                    , p4gf_p4msgid.MsgDm_LockBadUnicode
                    , p4gf_p4msgid.MsgDm_LockUtf16NotSupp
                    , p4gf_p4msgid.MsgDm_UnLockSuccess
                    , p4gf_p4msgid.MsgDm_UnLockAlready
                    , p4gf_p4msgid.MsgDm_UnLockAlreadyOther
                    , p4gf_p4msgid.MsgDm_OpenIsLocked
                    , p4gf_p4msgid.MsgDm_OpenXOpened
                    , p4gf_p4msgid.MsgDm_IntegXOpened
                    , p4gf_p4msgid.MsgDm_OpenWarnOpenStream
                    , p4gf_p4msgid.MsgDm_IntegMovedUnmapped
                    , p4gf_p4msgid.MsgDm_ExVIEW
                    , p4gf_p4msgid.MsgDm_ExVIEW2
                    , p4gf_p4msgid.MsgDm_ExPROTECT
                    , p4gf_p4msgid.MsgDm_ExPROTECT2
                    ]

# This subset of MSGID_CANNOT_OPEN identifies which errors are "current
# user lacks permission" errors. But the Git user doesn't know _which_
# user lacks permission. Tell them.
MSGID_EXPLAIN_P4USER   = [ p4gf_p4msgid.MsgDm_ExPROTECT
                         , p4gf_p4msgid.MsgDm_ExPROTECT2
                         ]
MSGID_EXPLAIN_P4CLIENT = [ p4gf_p4msgid.MsgDm_ExVIEW
                         , p4gf_p4msgid.MsgDm_ExVIEW2
                         ]

# timer/counter names
OVERALL = "Git to P4 Overall"
FAST_EXPORT = "FastExport"
TEST_BLOCK_PUSH = "Test Block Push"
CHECK_CONFLICT = "Check Conflict"
GIT_CHECKOUT = "Git Checkout"
COPY = "Copy"
COPY_BLOBS_1 = "Copy Blobs Pass 1"
COPY_BLOBS_2 = "Copy Blobs Pass 2"
CHECK_PROTECTS = "Check Protects"
MIRROR = "Mirror Git Objects"

N_BLOBS = "Number of Blobs"
N_RENAMES = "Number of Renames"


class ProtectsChecker:
    """class to handle filtering a list of paths against view and protections"""
    def __init__(self, ctx, author, pusher):
        """init P4.Map objects for author, pusher, view and combination"""
        self.ctx = ctx
        self.author = author
        self.pusher = pusher

        self.view_map = None
        self.read_protect_author = None
        self.read_protect_pusher = None
        self.read_filter = None
        self.write_protect_author = None
        self.write_protect_pusher = None
        self.write_filter = None

        self.init_view()
        self.init_read_filter()
        self.init_write_filter()

        self.author_denied = []
        self.pusher_denied = []
        self.unmapped = []

    def init_view(self):
        """init view map for client"""
        self.view_map = self.ctx.clientmap

    def init_read_filter(self):
        """init read filter"""
        self.read_protect_author = self.ctx.user_to_protect(self.author
                                        ).map_for_perm(p4gf_protect.READ)
        if not self.author == self.pusher:
            self.read_protect_pusher = self.ctx.user_to_protect(self.pusher
                                        ).map_for_perm(p4gf_protect.READ)
            self.read_filter = P4.Map.join(self.read_protect_author,
                                           self.read_protect_pusher)
        else:
            self.read_filter = self.read_protect_author
        self.read_filter = P4.Map.join(self.read_filter, self.view_map)

    def init_write_filter(self):
        """init write filter"""
        self.write_protect_author = self.ctx.user_to_protect(self.author
                                        ).map_for_perm(p4gf_protect.WRITE)
        if not self.author == self.pusher:
            self.write_protect_pusher = self.ctx.user_to_protect(self.pusher
                                        ).map_for_perm(p4gf_protect.WRITE)
            self.write_filter = P4.Map.join(self.write_protect_author,
                                            self.write_protect_pusher)
        else:
            self.write_filter = self.write_protect_author
        self.write_filter = P4.Map.join(self.write_filter, self.view_map)

    def filter_paths(self, blobs):
        """run list of paths through filter and set list of paths that don't pass"""
        # check against one map for read, one for write
        # if check fails, figure out if it was the view map or the protects
        # that caused the problem and report accordingly
        self.author_denied = []
        self.pusher_denied = []
        self.unmapped = []

        for blob in blobs:
            if blob['action'] == 'R' or blob['action'] == 'C':
                # for Rename or Copy, need to check read access for source path
                frompath = self.local_rel_path_to_client(blob['path'])
                if not self.read_filter.includes(frompath, False):
                    if not self.view_map.includes(frompath, False):
                        self.unmapped.append(frompath)
                    elif not self.read_protect_author.includes(frompath, False):
                        self.author_denied.append(frompath)
                    else:
                        self.pusher_denied.append(frompath)
                topath = self.local_rel_path_to_client(blob['topath'])
            else:
                topath = self.local_rel_path_to_client(blob['path'])

            # for all actions, need to check write access for dest path
            LOG.debug("toPath: "+topath)
            if not self.write_filter.includes(topath, False):
                if not self.view_map.includes(topath, False):
                    self.unmapped.append(topath)
                elif not self.write_protect_author.includes(topath, False):
                    self.author_denied.append(topath)
                else:
                    self.pusher_denied.append(topath)

    def local_rel_path_to_client(self, local_rel_path):
        """return client syntax path for local path"""
        return "//{0}/{1}".format(self.ctx.config.p4client, local_rel_path)

    def has_error(self):
        """return True if any paths not passed by filters"""
        return len(self.unmapped) or len(self.author_denied) or len(self.pusher_denied)

    def error_message(self):
        """return message indicating what's blocking the push"""
        if len(self.unmapped):
            return "file(s) not in client view"
        if len(self.author_denied):
            restricted_user = self.author if self.author else "<author>"
        elif len(self.pusher_denied):
            restricted_user = self.pusher if self.pusher else "<pusher>"
        else:
            restricted_user = "<unknown>"
        return "user '{}' not authorized to submit file(s) in git commit".format(restricted_user)


class G2P:
    """class to handle batching of p4 commands when copying git to p4"""
    def __init__(self, ctx):
        self.ctx = ctx
        self.addeditdelete = {}
        self.perf = p4gf_profiler.TimerCounterSet()
        self.perf.add_timers([OVERALL,
                             (FAST_EXPORT, OVERALL),
                             (TEST_BLOCK_PUSH, OVERALL),
                             (CHECK_CONFLICT, OVERALL),
                             (COPY, OVERALL),
                             (GIT_CHECKOUT, COPY),
                             (CHECK_PROTECTS, COPY),
                             (COPY_BLOBS_1, COPY),
                             (COPY_BLOBS_2, COPY),
                             (MIRROR, OVERALL),
                             ])
        self.perf.add_counters([N_BLOBS, N_RENAMES])
        self.usermap = p4gf_usermap.UserMap(ctx.p4gf)
        self.progress = ProgressReporter()

    def __str__(self):
        return "\n".join([str(self.perf),
                          str(self.ctx.mirror)
                         ])

    def revert_and_raise(self, errmsg):
        """An error occurred while attempting to submit the incoming change
        to Perforce. As a result, revert all modifications, log the error,
        and raise an exception."""
        # roll back and raise the problem to the caller
        p4 = connect_p4(user=p4gf_const.P4GF_USER, client=self.ctx.p4.client)
        if p4:
            opened = p4.run('opened')
            if opened:
                p4.run('revert', '//{}/...'.format(self.ctx.p4.client))
        # revert doesn't clean up added files
        self.remove_added_files()
        if not errmsg:
            errmsg = traceback.format_stack()
        msg = "import failed: {}".format(errmsg)
        LOG.error(msg)
        raise RuntimeError(msg)

    def _p4_message_to_text(self, msg):
        '''
        Convert a list of P4 messages to a single string.
        
        Annotate some errors with additional context such as P4USER.
        '''
        txt = str(msg)
        if msg.msgid in MSGID_EXPLAIN_P4USER:
            txt += ' P4USER={}.'.format(self.ctx.p4.user)
        if msg.msgid in MSGID_EXPLAIN_P4CLIENT:
            txt += ' P4USER={}.'.format(self.ctx.p4.client)
        return txt
        
    def check_p4_messages(self):
        """If the results indicate a file is locked by another user,
        raise an exception so that the overall commit will fail. The
        changes made so far will be reverted.
        """
        msgs = p4gf_p4msg.find_all_msgid(self.ctx.p4, MSGID_CANNOT_OPEN)
        if not msgs:
            return

        lines = [self._p4_message_to_text(m) for m in msgs]
        self.revert_and_raise('\n'.join(lines))

    def _p4run(self, cmd):
        '''
        Run one P4 command, logging cmd and results.
        '''
        p4 = self.ctx.p4
        LOG.getChild('p4.cmd').debug(" ".join(cmd))

        results = p4.run(cmd)

        if p4.errors:
            LOG.getChild('p4.err').error("\n".join(p4.errors))
        if p4.warnings:
            LOG.getChild('p4.warn').warning("\n".join(p4.warnings))
        LOG.getChild('p4.out').debug("{}".format(results))
        if LOG.getChild('p4.msgid').isEnabledFor(logging.DEBUG):
            log = LOG.getChild('p4.msgid')
            for m in p4.messages:
                log.debug(p4gf_p4msg.msg_repr(m))

        self.check_p4_messages()

    def run_p4_commands(self):
        """run all pending p4 commands"""
        for operation, paths in self.addeditdelete.items():
            cmd = operation.split(' ')
            # avoid writable client files problem by using -k and handling
            # the actual file action ourselves (in add/edit cases the caller
            # has already written the new file)
            if not cmd[0] == 'add':
                cmd.append('-k')
            if cmd[0] == 'move':
                # move takes a tuple of two arguments, the old name and new name
                oldnames = [escape_path(pair[0]) for pair in paths]
                # move requires opening the file for edit first
                self._p4run(['edit', '-k'] + oldnames)
                LOG.debug("Edit {}".format(oldnames))
                for pair in paths:
                    (frompath, topath) = pair
                    self._p4run(['move', '-k', escape_path(frompath), escape_path(topath)])
                    LOG.debug("Move from {} to {}".format(frompath, topath))
            else:
                reopen = []
                if 'edit -t' in operation:
                    # edit -t text does not work, must 'edit' then 'reopen -t'
                    # "can't change from xtext - use 'reopen'"
                    reopen = ['reopen', '-t', cmd[2]]
                    cmd = cmd[0:1] + cmd[3:]

                if not cmd[0] == 'add':
                    self._p4run(cmd + [escape_path(path) for path in paths])
                else:
                    self._p4run(cmd + paths)

                if reopen:
                    self._p4run(reopen + [escape_path(path) for path in paths])

                if cmd[0] == 'delete':
                    LOG.debug("Delete {}".format(paths))
                    for path in paths:
                        os.remove(path)

    def remove_added_files(self):
        """remove added files to restore p4 client after failure of p4 command"""
        for operation, paths in self.addeditdelete.items():
            cmd = operation.split(' ')
            if cmd[0] == 'add':
                for path in paths:
                    os.unlink(path)

    def setup_p4_command(self, command, p4path):
        """Add command to list to be run by run_p4_commands. If the command
        is 'move' then the p4path is expected to be a tuple of the frompath
        and topath."""
        if command in self.addeditdelete:
            self.addeditdelete[command].append(p4path)
        else:
            self.addeditdelete[command] = [p4path]

    def _toggle_filetype(self, p4path, isx):
        """Returns the new file type for the named file, switching the
        executable state based on the isx value.

        Args:
            p4path: Path of the file to modify.
            isx: True if currently executable.

        Returns:
            New type for the file; may be None.
        """
        p4type = None
        if isx:
            p4type = '+x'
        else:
            # To remove a previously assigned modifier, the whole filetype
            # must be specified.
            for tipe in ['headType', 'type']:
                # For a file that was executable, is being renamed (with
                # edits), and is no longer executable, we need to handle the
                # fact that it's not yet in Perforce and so does not have a
                # headType.
                try:
                    p4type = p4gf_util.first_value_for_key(
                                self.ctx.p4.run(['fstat', '-T' + tipe, p4path]),
                                tipe)
                except P4.P4Exception:
                    pass
                if p4type:
                    p4type = p4gf_p4filetype.remove_mod(p4type, 'x')
        return p4type

    def add_or_edit_blob(self, blob):
        """run p4 add or edit for a new or modified file"""

        # get local path in p4 client
        p4path = self.ctx.contentlocalroot + blob['path']

        # edit or add?
        isedit = os.path.exists(p4path)

        # make sure dest dir exists
        dstdir = os.path.dirname(p4path)
        if not os.path.exists(dstdir):
            os.makedirs(dstdir)

        if isedit:
            LOG.debug("Copy edit from: " + blob['path'] + " to " + p4path)
            # for edits, only use +x or -x to propagate partial filetype changes
            wasx = os.stat(p4path).st_mode & stat.S_IXUSR
            isx = os.stat(blob['path']).st_mode & stat.S_IXUSR
            if wasx != isx:
                p4type = self._toggle_filetype(p4path, isx)
            else:
                p4type = None
            if p4type:
                LOG.debug("  set filetype: {ft}  oldx={oldx} newx={newx}"
                          .format(ft=p4type,
                                  oldx=wasx,
                                  newx=isx))
            shutil.copystat(blob['path'], p4path)
            shutil.copyfile(blob['path'], p4path)
        else:
            LOG.debug("Copy add from: " + blob['path'] + " to " + p4path)
            # for adds, use complete filetype of new file
            p4type = p4type_from_mode(blob['mode'])
            shutil.copyfile(blob['path'], p4path)

        # if file exists it's an edit, so do p4 edit before copying content
        # for an add, do p4 add after copying content
        p4type = ' -t ' + p4type if p4type else ''
        if isedit:
            self.setup_p4_command("edit" + p4type, p4path)
        else:
            self.setup_p4_command("add -f" + p4type, p4path)

    def rename_blob(self, blob):
        """ run p4 move for a renamed/moved file"""
        self.perf.counter[N_RENAMES] += 1

        # get local path in p4 client
        p4frompath = self.ctx.contentlocalroot + blob['path']
        p4topath = self.ctx.contentlocalroot + blob['topath']

        # ensure destination directory exists
        dstdir = os.path.dirname(p4topath)
        if not os.path.exists(dstdir):
            os.makedirs(dstdir)
        # copy out of Git repo to Perforce workspace
        shutil.copyfile(blob['topath'], p4topath)
        self.setup_p4_command("move", (p4frompath, p4topath))

    def copy_blob(self, blob):
        """run p4 integ for a copied file"""
        self.perf.counter[N_BLOBS] += 1

        # get local path in p4 client
        p4frompath = self.ctx.contentlocalroot + blob['path']
        p4topath = self.ctx.contentlocalroot + blob['topath']

        self._p4run(["copy", "-v", escape_path(p4frompath), escape_path(p4topath)])

        # make sure dest dir exists
        dstdir = os.path.dirname(p4topath)
        if not os.path.exists(dstdir):
            os.makedirs(dstdir)

        LOG.debug("Copy/integ from: " + p4frompath + " to " + p4topath)
        shutil.copyfile(p4frompath, p4topath)

    def delete_blob(self, blob):
        """run p4 delete for a deleted file"""

        # get local path in p4 client
        p4path = self.ctx.contentlocalroot + blob['path']
        self.setup_p4_command("delete", p4path)

    def copy_blobs(self, blobs):
        """copy git blobs to perforce revs"""
        # first, one pass to do rename/copy
        # these don't batch.  move can't batch due to p4 limitations.
        # however, the edit required before move is batched.
        # copy could be batched by creating a temporary branchspec
        # but for now it's done file by file
        with self.perf.timer[COPY_BLOBS_1]:
            for blob in blobs:
                if blob['action'] == 'R':
                    self.rename_blob(blob)
                elif blob['action'] == 'C':
                    self.copy_blob(blob)
            self.run_p4_commands()
        # then, another pass to do add/edit/delete
        # these are batched to allow running the minimum number of
        # p4 commands.  That means no more than one delete, one add per
        # filetype and one edit per filetype.  Since we only support three
        # possible filetypes (text, text+x, symlink) there could be at most
        # 1 + 3 + 3 commands run.
        with self.perf.timer[COPY_BLOBS_2]:
            self.addeditdelete = {}
            for blob in blobs:
                if blob['action'] == 'M':
                    self.add_or_edit_blob(blob)
                elif blob['action'] == 'D':
                    self.delete_blob(blob)
            self.run_p4_commands()

    def check_protects(self, p4user, blobs):
        """check if author is authorized to submit files"""
        pc = ProtectsChecker(self.ctx, self.ctx.authenticated_p4user, p4user)
        pc.filter_paths(blobs)
        if pc.has_error():
            self.revert_and_raise(pc.error_message())

    def _reset_for_new_commit(self):
        """
        Clear out state from previous commit that must not carry over
        into next commit.
        """
        self.addeditdelete = {}

    def attempt_resync(self):
        """Attempts to sync -k the Git Fusion client to the change that
        corresponds to the HEAD of the Git mirror repository. This prevents
        the obscure "file(s) not on client" error.
        """
        # we assume we are in the GIT_WORK_TREE, which seems to be a safe
        # assumption at this point
        try:
            last_commit = p4gf_util.git_ref_master()
            if last_commit:
                last_changelist_number = self.ctx.mirror.get_change_for_commit(
                    last_commit, self.ctx)
                if last_changelist_number:
                    filerev = "//...@{}".format(last_changelist_number)
                    self._p4run(['sync', '-k', filerev])
        except P4.P4Exception:
            # don't stop the world if we have an error above
            LOG.warn("resync failed with exception", exc_info=True)

    def copy_commit(self, commit):
        """copy a single commit"""

        self._reset_for_new_commit()

        #OG.debug("dump commit {}".format(commit))
        LOG.debug("for  commit {}".format(commit['mark']))
        LOG.debug("with description: {}".format(commit['data']))
        LOG.debug("files affected: {}".format(commit['files']))

        # Reject merge commits. Not supported in 2012.1.
        if 'merge' in commit:
            self.revert_and_raise(("Merge commit {} not permitted."
                                   +" Rebase to create a linear"
                                   +" history.").format(commit['sha1']))

        # strip any enclosing angle brackets from the email address
        email = commit['author']['email'].strip('<>')
        user = self.usermap.lookup_by_email(email)
        LOG.debug("for email {} found user {}".format(email, user))
        if (user is None) or (not self.usermap.p4user_exists(user[0])):
            # User is not a known and existing Perforce user, and the
            # unknown_git account is not set up, so reject the commit.
            self.revert_and_raise("User '{}' not permitted to commit".format(email))
        author_p4user = user[0]

        for blob in commit['files']:
            err = check_valid_filename(blob['path'])
            if err:
                self.revert_and_raise(err)

        with self.perf.timer[GIT_CHECKOUT]:
            d = p4gf_util.popen_no_throw(['git', 'checkout', commit['sha1']])
            if d['Popen'].returncode:
                # Sometimes git cannot distinquish the revision from a path...
                p4gf_util.popen(['git', 'reset', '--hard', commit['sha1'], '--'])

        with self.perf.timer[CHECK_PROTECTS]:
            self.check_protects(author_p4user, commit['files'])

        try:
            self.copy_blobs(commit['files'])
        except P4.P4Exception as e:
            self.revert_and_raise(str(e))

        with self.perf.timer[COPY_BLOBS_2]:
            pusher_p4user = self.ctx.authenticated_p4user
            LOG.debug("Pusher is: {}, author is: {}".format(pusher_p4user, author_p4user))
            desc = change_description(commit, pusher_p4user, author_p4user)

            try:
                opened = self.ctx.p4.run('opened')
                if opened:
                    changenum = p4_submit(self.ctx.p4, desc, author_p4user,
                                          commit['author']['date'])
                    LOG.info("Submitted change @{} for commit {}".format(changenum, commit['sha1']))
                else:
                    LOG.info("Ignored empty commit {}".format(commit['sha1']))
                    return None
            except P4.P4Exception as e:
                self.revert_and_raise(str(e))
            return ":" + str(changenum) + " " + commit['sha1']

    def test_block_push(self):
        """Test hook to temporarily block and let test script
        introduce conflicting changes.
        """
        s = p4gf_util.test_vars().get(p4gf_const.P4GF_TEST_BLOCK_PUSH)
        if not s:
            return

        log = logging.getLogger("test_block_push")
        block_dict = p4gf_util.test_var_to_dict(s)
        log.debug(block_dict)

        # Fetch ALL the submitted changelists as of right now.
        log.debug("p4 changes {}".format(p4gf_path.slash_dot_dot_dot(self.ctx.config.p4client)))
        cl_ay = self.ctx.p4.run('changes',
                                '-l',
                                p4gf_path.slash_dot_dot_dot(self.ctx.config.p4client))

        # Don't block until after something?
        after = block_dict['after']
        if after:
            if not contains_desc(after, cl_ay):
                log.debug("Do not block until after: {}".format(after))
                return

        until = block_dict['until']
        log.debug("BLOCKING. Seen        'after': {}".format(after))
        log.debug("BLOCKING. Waiting for 'until': {}".format(until))

        changes_path_at = ("{path}@{change},now"
                           .format(path=p4gf_path.slash_dot_dot_dot(self.ctx.config.p4client),
                                   change=cl_ay[-1]['change']))

        while not contains_desc(until, cl_ay):
            time.sleep(1)
            cl_ay = self.ctx.p4.run('changes', changes_path_at)

        log.debug("Block released")
        
    def copy(self, start_at, end_at):
        """copy a set of commits from git into perforce"""
        with self.perf.timer[OVERALL]:
            with p4gf_util.HeadRestorer():
                LOG.debug("begin copying from {} to {}".format(start_at, end_at))
                self.attempt_resync()
                with self.perf.timer[CHECK_CONFLICT]:
                    conflict_checker = G2PConflictChecker(self.ctx)
                with self.perf.timer[FAST_EXPORT]:
                    fe = p4gf_fastexport.FastExport(start_at, end_at, self.ctx.tempdir.name)
                    fe.run()
                marks = []
                commit_count = 0
                for x in fe.commands:
                    if x['command'] == 'commit':
                        commit_count += 1
                self.progress.progress_init_determinate(commit_count)
                try:
                    for command in fe.commands:
                        with self.perf.timer[TEST_BLOCK_PUSH]:
                            self.test_block_push()
                        if command['command'] == 'commit':
                            self.progress.progress_increment("Copying changelists...")
                            self.ctx.heartbeat()
                            with self.perf.timer[COPY]:
                                mark = self.copy_commit(command)
                                if mark is None:
                                    continue
                            with self.perf.timer[CHECK_CONFLICT]:
                                (git_commit_sha1,
                                 p4_changelist_number) = mark_to_commit_changelist(mark)
                                conflict_checker.record_commit(git_commit_sha1,
                                                               p4_changelist_number)
                                if conflict_checker.check():
                                    LOG.error("P4 conflict found")
                                    break
                            marks.append(mark)
                        elif command['command'] == 'reset':
                            pass
                        else:
                            raise RuntimeError("Unexpected fast-export command: " +
                                               command['command'])
                finally:
                    # we want to write mirror objects for any commits that made it through
                    # any exception will still be alive after this
                    with self.perf.timer[MIRROR]:
                        self.ctx.mirror.add_commits(marks)
                        self.ctx.mirror.add_objects_to_p4(self.ctx)

                if conflict_checker.has_conflict():
                    raise RuntimeError("Conflicting change from Perforce caused one"
                                       + " or more git commits to fail. Time to"
                                       + " pull, rebase, and try again.")

        LOG.getChild("time").debug("\n" + str(self))


def copy_git_changes_to_p4(ctx, start_at, end_at):
    """copy a set of commits from git into perforce"""
    g2p = G2P(ctx)
    g2p.copy(start_at, end_at)
