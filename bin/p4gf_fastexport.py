#! /usr/bin/env python3.2
"""FastExport class"""

import re
import tempfile
from subprocess import check_output

import p4gf_log

SP = b' ' 
LF = b'\n'
SPLT = b" <"

LOG = p4gf_log.for_module()



def unescape_unicode(match):
    """
    given a match of an octal backslash escaped character,
    return a bytearray containing that character
    """
    return bytearray([int(match.group(0)[1:], 8)])

def remove_backslash_escapes(ba):
    """
    given an bytearray with a path escaped by git-fast-export
    return an unescaped string
    
    quotes are escaped as \"
    unicode chars are escaped utf8, with \ooo for each byte
    """
    # pylint: disable=W1401
    # Anomalous backslash in string
    ba = re.sub(b'\\\\\d{3}', unescape_unicode, ba)
    ba = ba.replace(b'\\"', b'"')
    return ba.decode()


class Parser:
    """A parser for git fast-import/fast-export scripts"""
    def __init__(self, text, marks):
        self.text = text.encode()
        self.marks = marks
        self.offset = 0

    def at_end(self):
        """return TRUE if at end of input, else FALSE"""
        return self.offset == len(self.text)

    def peek_token(self, separator):
        """return the next token or None, without advancing position"""
        sep = self.text.find(separator, self.offset)
        if sep == -1:
            return None
        return self.text[self.offset:sep].decode()

    def get_token(self, separator):
        """return the next token, advancing position

        If no token available, raises error

        If separator is more than one char, first char is the actual
        separator and rest is lookahead, so offset will be left pointing
        at second char of 'separator'.
        """
        sep = self.text.find(separator, self.offset)
        if sep == -1:
            raise RuntimeError("error parsing git-fast-export: expected '" +
                               separator.decode() + "'")
        token = self.text[self.offset:sep].decode()
        self.offset = sep + 1
        return token

    def get_path_token(self, separator):
        """return the next token with quotes removed, advancing position

        Paths may be quoted in fast-import/export scripts.
        """
        # In git-fast-export, paths may be double-quoted and any double-quotes
        # in the path are slash-escaped (e.g. "foo\"bar.txt").
        offset = self.offset
        if self.text[offset:offset + 1] != b'"':
            return self.get_token(separator)
        escaped = False
        end = 0
        for offset in range(self.offset + 1, len(self.text)):
            if escaped:
                escaped = False
            elif self.text[offset:offset + 1] == b'\\':
                escaped = True
            elif self.text[offset:offset + 1] == b'"':
                end = offset + 1
                break
        if self.text[end:end+len(separator)] != separator:
            raise RuntimeError("error parsing git-fast-export: expected '" +
                               separator.decode() + "'")
        token = self.text[self.offset:end].strip(b'"')
        self.offset = end + 1
        # remove any slash-escapes since they are not needed from here on
        # also undo any escaping of unicode chars that git-fast-export did
        token = remove_backslash_escapes(token)
        return token

    def skip_optional_lf(self):
        """skip next char if it's a LF"""
        if self.text[self.offset:self.offset + 1] == LF:
            self.offset = self.offset + 1

    def get_data(self):
        """read a git style string: <size> SP <string> [LF]"""
        self.get_token(SP)
        count = int(self.get_token(LF))
        string = self.text[self.offset:self.offset + count].decode()
        self.offset += count
        self.skip_optional_lf()
        return string

    def get_command(self):
        """read a command

        raises error if it's not an expected command
        """
        command = self.get_token(SP)
        if command == "reset":
            return self.get_reset()
        if command == "commit":
            return self.get_commit()
        raise RuntimeError("error parsing git-fast-export: unexpected command " +
                           command)

    def get_reset(self):
        """read the body of a reset command"""
        ref = self.get_token(LF)
        LOG.debug("get_reset ref={}".format(ref))
        return {"command": "reset", "ref": ref}

    def get_commit(self):
        """read the body of a commit command"""
        LOG.debug("Commit text: {}".format(self.text[self.offset:300+self.offset]))
        ref = self.get_token(LF)
        result = {"command": "commit", "ref": ref, "files": []}
        while True:
            next_token = self.peek_token(SP)
            if next_token == "mark":
                self.get_token(SP)
                result["mark"] = self.get_token(LF)[1:]
                result["sha1"] = self.marks[result["mark"]]
            elif next_token == "author" or next_token == "committer":
                tag = self.get_token(SP)
                value = {}
                value["user"] = self.get_token(SPLT)
                value["email"] = self.get_token(SP)
                value["date"] = self.get_token(SP)
                value["timezone"] = self.get_token(LF)
                result[tag] = value
            elif next_token == "data":
                result["data"] = self.get_data()
            elif next_token == "from":
                self.get_token(SP)
                result["from"] = self.get_token(LF)[1:]
            elif next_token == "merge":
                self.get_token(SP)
                result["merge"] = self.get_token(LF)[1:]
            elif next_token == "M":
                value = {"action": self.get_token(SP)}
                value["mode"] = self.get_token(SP)
                value["sha1"] = self.get_token(SP)
                value["path"] = self.get_path_token(LF)
                result["files"].append(value)
            elif next_token == "D":
                value = {"action": self.get_token(SP)}
                value["path"] = self.get_path_token(LF)
                result["files"].append(value)
            elif next_token == "R":
                value = {"action": self.get_token(SP)}
                value["path"] = self.get_path_token(SP)
                value["topath"] = self.get_path_token(LF)
                result["files"].append(value)
            elif next_token == "C":
                value = {"action": self.get_token(SP)}
                value["path"] = self.get_path_token(SP)
                value["topath"] = self.get_path_token(LF)
                result["files"].append(value)
            else:
                break
        self.skip_optional_lf()
        LOG.debug("Extracted commit: {}".format(result))
        return result


class FastExport:
    """Run git-fast-export to create a list of objects to copy to Perforce.

    last_old_commit is the last commit copied from p4 -> git
    last_new_commit is the last commit you want to copy from git -> p4
    """

    def __init__(self, last_old_commit, last_new_commit, tempdir):
        if last_old_commit != '0'*40:
            # 0000000 ==> NO old commit, export starting with very first commit.
            self.last_old_commit = last_old_commit
        else:
            self.last_old_commit = None
        self.last_new_commit = last_new_commit
        self.tempdir = tempdir
        self.script = ""
        self.marks = None
        self.commands = None

    def write_marks(self):
        """write a single sha1 to the marks file for the last commit"""
        marksfile = tempfile.NamedTemporaryFile(dir=self.tempdir)
        if self.last_old_commit:
            marksfile.write((":1 " + self.last_old_commit + "\n").encode())
        marksfile.flush()
        return marksfile

    def read_marks(self, marksfile):
        """read list of sha1 from marks file created by git-fast-export"""
        marks = marksfile.readlines()
        self.marks = {}
        for mark in marks:
            parts = mark.decode().split(" ")
            marknum = parts[0][1:]
            sha1 = parts[1].strip()
            self.marks[marknum] = sha1

    def parse_commands(self):
        """parse commands from script"""
        p = Parser(self.script, self.marks)
        self.commands = []
        while not p.at_end():
            self.commands.append(p.get_command())

    def run(self):
        """Run git-fast-export"""

        import_marks = self.write_marks()
        export_marks = tempfile.NamedTemporaryFile(dir=self.tempdir)

        # Note that we do not ask Git to attempt to detect file renames or
        # copies, as this seems to lead to several bugs, including one that
        # loses data. For now, the safest option is to translate the file
        # operations exactly as they appear in the commit. This also makes the
        # round-trip conversion safer.
        cmd = ['git', 'fast-export', '--no-data']
        cmd.append("--import-marks={}".format(import_marks.name))
        cmd.append("--export-marks={}".format(export_marks.name))
        if self.last_old_commit:
            cmd.append("{}..{}".format(self.last_old_commit, self.last_new_commit))
        else:
            cmd.append(self.last_new_commit)

        # work around pylint bug where it doesn't know check_output() returns encoded bytes
        self.script = check_output(cmd).decode()    # pylint: disable=E1103,E1101
        self.read_marks(export_marks)
        self.parse_commands()
