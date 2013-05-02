#! /usr/bin/env python3.2
""" P4File class"""
import p4gf_util

def update_type_string(old_type):
    """convert old style perforce type name to new style"""
    old_filetypes = {
    "ctempobj": "binary+Sw",
    "ctext": "text+C",
    "cxtext": "text+Cx",
    "ktext": "text+k",
    "kxtext": "text+kx",
    "ltext": "text+F",
    "tempobj": "binary+FSw",
    "ubinary": "binary+F",
    "uresource": "resource+F",
    "uxbinary": "binary+Fx",
    "xbinary": "binary+x",
    "xltext": "text+Fx",
    "xtempobj": "binary+Swx",
    "xtext": "text+x",
    "xunicode": "unicode+x",
    "xutf16": "utf16+x",
    }
    if old_type in old_filetypes:
        return old_filetypes[old_type]
    return old_type


def has_type_modifier(typestring, modifier):
    """check a perforce filetype for a +modifier, e.g. +x"""

    parts = update_type_string(typestring).split('+')
    if len(parts) < 2:
        return False
    return parts[1].find(modifier) != -1


class P4File:
    """A file, as reported by p4 describe or p4 sync

    Also contains SHA1 of file content, if that has been set.
    """

    def __init__(self):
        self.depot_path = None
        self.client_path = None
        self.action = None
        self.revision = None
        self.sha1 = ""
        self.type = ""
        self.change = None

    @staticmethod
    def create_from_describe(vardict, index):
        """Create P4File from p4 describe

        Describe does not report the client path, but that will be
        reported later by p4 sync and set on the P4File at that time.
        """

        f = P4File()
        f.depot_path = vardict["depotFile"][index]
        f.type = vardict["type"][index]
        f.action = vardict["action"][index]
        f.revision = vardict["rev"][index]
        return f

    @staticmethod
    def create_from_sync(vardict):
        """Create P4File from p4 sync

        This is used for the initial snapshot of a p4 -> git copy.
        In this situation, we want to treat all files which are not
        being deleted by this sync as adds.  Files which are being
        deleted should be filtered out before getting here.

        Sync does not report file type, so leave that blank and it
        will get filled in later by using fstat.
        """

        f = P4File()
        f.depot_path = vardict["depotFile"]
        f.client_path = vardict["clientFile"]
        if vardict["action"] == 'deleted':
            f.action = "delete"
        else:
            f.action = "add"
        f.revision = vardict["rev"]
        return f

    @staticmethod
    def _fstat_attr_map():
        """Return a map from fstat col to P4File attribute name."""
        return {"depotFile"  : "depot_path",
                "clientFile" : "client_path",
                "headAction" : "action",
                "headRev"    : "revision",
                "headType"   : "type",
                "headChange" : "change"
                }

    @staticmethod
    def fstat_cols():
        """Return a string list of fields that create_from_fstat() requires."""
        return list(P4File._fstat_attr_map().keys())

    @staticmethod
    def create_from_fstat(vardict):
        """Create P4File from p4 fstat
        """

        f = P4File()
        p4gf_util.dict_to_attr(vardict, P4File._fstat_attr_map(), f)
        return f

    @staticmethod
    def create_from_print(vardict):
        """Create P4File from p4 print
        """

        f = P4File()
        f.depot_path = vardict["depotFile"]
        f.action = vardict["action"]
        f.revision = vardict["rev"]
        f.type = vardict["type"]
        f.change = vardict["change"]
        return f

    def is_delete(self):
        """return True if fie is deleted at this revision"""
        return self.action == "delete" or self.action == "move/delete"

    def rev_path(self):
        """return depotPath#rev"""
        return self.depot_path + "#" + self.revision

    def is_k_type(self):
        """return True if file type uses keyword expansion"""
        return has_type_modifier(self.type, "k")

    def is_x_type(self):
        """return True if file is executable type"""
        return has_type_modifier(self.type, "x")

    def is_symlink(self):
        """return True if file is a symlink type"""
        return self.type.startswith("symlink")

    def __str__(self):
        return self.depot_path

    def __repr__(self):
        if (self.client_path):
            client_path = self.client_path
        else:
            client_path = ""
        return "\n".join(["depot_path : " + self.depot_path,
                          "revision   : " + self.revision,
                          "client_path: " + client_path,
                          "type       : " + self.type,
                          "action     : " + self.action,
                          "sha1       : " + self.sha1])
