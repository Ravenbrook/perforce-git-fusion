#! /usr/bin/env python3.2
"""Git Fusion package constants."""

import os

# 
# Package-level constants
#
P4GF_CLIENT_PREFIX = "git-fusion-"
P4GF_GROUP         = "git-fusion-group"
P4GF_USER          = "git-fusion-user"
P4GF_OBJECT_CLIENT_PREFIX = "git-fusion--"
P4GF_DEPOT         = ".git-fusion"
P4GF_UNKNOWN_USER  = "unknown_git"

P4GF_GROUP_VIEW_PULL  = "git-fusion-{view}-pull"
P4GF_GROUP_VIEW_PUSH  = "git-fusion-{view}-push"
P4GF_GROUP_PULL       = "git-fusion-pull"
P4GF_GROUP_PUSH       = "git-fusion-push"

P4GF_COUNTER_INIT_STARTED = "git-fusion-init-started"
P4GF_COUNTER_INIT_COMPLETE = "git-fusion-init-complete"
P4GF_COUNTER_PERMISSION_GROUP_DEFAULT = "git-fusion-permission-group-default"

P4GF_BRANCH_EMPTY_REPO = "p4gf_empty_repo"
P4GF_BRANCH_TEMP       = "git_fusion_temp_branch"

# Environment vars
P4GF_AUTH_P4USER      = "P4GF_AUTH_P4USER"

# Internal debugging keys
P4GF_TEST             = "test"                  # section in rc file for test vars
P4GF_TEST_BLOCK_PUSH  = "p4gf_test_block_push"  # p4gf_copy_to_p4.copy_git_changes_to_p4()
                    # Sleep this many seconds after lock acquisition
                    # succeeds for a log that contains string "view"
P4GF_TEST_LOCK_VIEW_SLEEP_AFTER_ACQUIRE_SECONDS = \
                                 "p4gf_test_lock_view_sleep_after_acquire_seconds"
                    # Record long-held locks after N seconds
P4GF_TEST_LOCK_LOG_AFTER_HELD_SECONDS = "p4gf_test_lock_log_after_held_seconds"
                    # Slow down any process that updates the heartbeat counter.
P4GF_TEST_LOCK_VIEW_SLEEP_AFTER_HEARTBEAT_SECONDS = \
                            "p4gf_test_lock_view_sleep_after_heartbeat_seconds"


# Internal testing environment variables.
                    # Read config from here, not /etc/git-fusion.log.conf
P4GF_TEST_LOG_CONFIG_PATH = "P4GF_LOG_CONFIG_FILE"   
P4GF_TEST_RC_PATH         = "P4GF_TEST_RC_PATH"

# Filenames
P4GF_DIR              = '.git-fusion'
P4GF_RC_FILE          = '.git-fusion-rc'
P4GF_TEMP_DIR_PREFIX  = 'p4gf_'

# Placed in change description when importing from Git to Perforce.
P4GF_IMPORT_HEADER    = "Imported from Git"


# 'git clone' of these views (or pulling or fetching or pushing) runs special commands
P4GF_UNREPO_INFO      = '@info'     # Returns our version text
P4GF_UNREPO_LIST      = '@list'     # Returns list of repos visible to user
P4GF_UNREPO_HELP      = '@help'     # Returns contents of help.txt, if present

# Now import any environemnt variables starting P4GF_ so that they can be overridden
# by local customisations.
locals().update({key:value for key, value in os.environ.items() if key.startswith("P4GF_")})

