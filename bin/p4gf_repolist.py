#! /usr/bin/env python3.2
'''get list of repos'''

import p4gf_group
import p4gf_util


class RepoList:
    '''build list of repos available to user'''

    def __init__(self):
        '''empty list'''
        self.repos = []

    @staticmethod
    def list_for_user(p4, user):
        '''build list of repos visible to user'''
        result = RepoList()

        for view in p4gf_util.view_list(p4):
            #check user permissions for view
            view_perm = p4gf_group.ViewPerm.for_user_and_view(p4,
                                                            user,
                                                            view)
            #sys.stderr.write("view: {}, user: {}, perm: {}".format(view, user, view_perm))
            if view_perm.can_push():
                result.repos.append((view, 'push'))
            elif view_perm.can_pull():
                result.repos.append((view, 'pull'))
        result.repos.sort(key=lambda tup: tup[0])
        return result
