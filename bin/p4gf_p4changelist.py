#! /usr/bin/env python3.2
"""P4Changelist class"""
import logging
import p4gf_log
import p4gf_util

from p4gf_p4file import P4File

LOG = p4gf_log.for_module()


class P4Changelist:
    """a changelist, as reported by p4 describe

        Run p4 describe of a changelist and filter the files reported
        against a specified root path, e.g. //depot/main/p4/
        """

    def __init__(self):
        self.change = None
        self.description = None
        self.user = None
        self.time = None
        self.files = []   # P4Files in this changelist

    @staticmethod
    def create_using_describe(p4, change, depot_root):
        """create a P4Changelist by running p4 describe"""

        result = p4.run("describe", "-s", str(change))
        cl = P4Changelist()
        vardict = p4gf_util.first_dict_with_key(result, 'change')
        cl.change = vardict["change"]
        cl.description = vardict["desc"]
        cl.user = vardict["user"]
        cl.time = vardict["time"]
        for i in range(len(vardict["depotFile"])):
            p4file = P4File.create_from_describe(vardict, i)
            # filter out files not under our root right now
            if not p4file.depot_path.startswith(depot_root):
                continue
            cl.files.append(p4file)
        return cl

    @staticmethod
    def create_using_changes(vardict):
        """create a P4Changelist from the output of p4 changes"""

        cl = P4Changelist()
        cl.change = vardict["change"]
        cl.description = vardict["desc"]
        cl.user = vardict["user"]
        cl.time = vardict["time"]
        return cl

    @staticmethod
    def create_changelist_list_as_dict(p4, path):
        """Run p4 changes to get a list of changes, return that as a dict
           indexed by changelist number (as string).

        Returns a dict["change"] ==> P4Changelist

        p4: initialized P4 object
        path: path + revision specifier, e.g. //depot/main/p4/...@1,#head
        """
        cmd = ["changes", "-l", path]
        LOG.debug("create_changelist_list_as_dict: p4 {}"
                  .format(' '.join(cmd)))
        changes_result = p4.run(cmd)
        changes = {}
        for vardict in changes_result:
            change = P4Changelist.create_using_changes(vardict)
            changes[change.change] = change
        if LOG.isEnabledFor(logging.DEBUG):
            cll = [cl['change'] for cl in changes_result]
            if 10 < len(cll):
                cll = cll[0:9] + ['...{} total'.format(len(cll))]
            LOG.debug("create_changelist_list_as_dict returning\n{}"
                      .format(' '.join(cll)))
        return changes

    def file_from_depot_path(self, depot_path):
        """return P4File from files list which matches depot_path or None"""
        for p4file in self.files:
            if p4file.depot_path == depot_path:
                return p4file
        return None

    def __str__(self):
        return "change {0} with {1} files".format(self.change, len(self.files))

    def __repr__(self):
        files = [repr(p4file) for p4file in self.files]
        result = "\n".join(["change: " + self.change,
                            "description: " + self.description,
                            "user: " + self.user,
                            "time: " + self.time,
                            "files:",
                            ] + files)
        return result
