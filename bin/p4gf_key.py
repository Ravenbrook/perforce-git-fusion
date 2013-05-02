#! /usr/bin/env python3.2
"""Perforce API dict keys

CASE IS SIGNIFICANT, and is reflected in the symbol name.
Most list operations ('users','depots') return dicts whose keys begin with
lowercase, but their analogous singular commands ('user', 'depot') return and
expect keys that begin with capitals.
"""

# pylint:disable=C0103
# Invalid name
# Case is significant and more important than your CAPS-loving pep8.
Value = 'Value'
