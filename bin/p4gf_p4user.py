#! /usr/bin/env python3.2
""" P4User class"""


class P4User:
    """A user, as reported by p4 users.

    """

    def __init__(self):
        self.name = None
        self.email = None
        self.full_name = None

    def __str__(self):
        return "\n".join(["name     : " + self.name,
                          "email    : " + self.email,
                          "full_name: " + self.full_name])

    def __repr__(self):
        return str(self)
