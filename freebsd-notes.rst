FreeBSD Installation Notes
==========================
Richard Brooksby, Ravenbrook Limited, 2013-05-02

This guide is not a substitute for reading the Git Fusion
Administrator's Guide
<http://www.perforce.com/perforce/doc.current/manuals/p4-git-fusion-admin/03_install_tgz.html>.
Good luck with that.  You will need to ignore or modify all instructions
concerning ``.bashrc`` or using Bash-specific syntax like ``declare
-x``.  See below for how to set up your environment.

Also, don't blindly execute these commands.  This is a summary of how we
did it, not a perfect script.

If you have corrections or updates to this guide, please submit issues
or pull requests via GitHub.

1. Create a user to run Git Fusion.  This will be the name that appears
   in Git URLs.  We chose ``git-fusion``, so our URL is
   ``git-fusion@raven.ravenbrook.com``.  This user is unprivileged.

2. Edit the git-fusion user's .profile to set the variables required by
   the Git Fusion scripts.  For example::

    PATH="$HOME/bin:$HOME/src/git-fusion/bin:/usr/local/bin:/usr/bin:/bin" export PATH
    P4USER=git-fusion-user export P4USER
    P4PORT=perforce:1666 export P4PORT

3. Get Git Fusion.  You might want our customised version::

    $ cd /home/git-fusion
    $ mkdir src
    $ cd src
    $ git clone git://github.com/Ravenbrook/perforce-git-fusion.git

4. Git Fusion requires an old version of Git.  FreeBSD's version is too
   new.  Make the old version into ~git-fusion/bin like this::

    $ git clone https://github.com/git/git.git
    $ cd git
    $ git checkout v1.7.11.3
    $ gmake
    $ gmake install
    $ git --version
    git version 1.7.11.3

5. Git Fusion requires Python 3.2.  FreeBSD won't have this by default::

    # cd /usr/ports/lang/python32
    # make install

6. You'll also need to set up P4Python under Python 3.2::

    $ cd /home/git-fusion/src
    $ curl -O ftp://ftp.perforce.com/perforce/r13.1/bin.freebsd70x86/p4api.tgz | tar xzf -
    $ curl -O ftp://ftp.perforce.com/perforce/r12.1/bin.tools/p4python.tgz
    $ python3.2 setup.py build --apidir /home/git-fusion/src/p4api
    $ python3.2 p4test.py

    # python3.2 setup.py install --apidir /home/git-fusion/src/p4api

7. Follow the admin guide starting at "Establishing Git Fusion data in
   the Perforce server" to set up the Perforce repository
   <http://www.perforce.com/perforce/doc.current/manuals/p4-git-fusion-
   admin/03_install_tgz.html#1104707>.

8. Set up a crontab to keep the SSH keys up-to-date.  Ours looks like this::

    # min	hour	day	month	dow	command
    */5	*	*	*	*	. .profile && p4gf_auth_update_authorized_keys.py

9. Continue with "Configuring Git Fusion"
   <http://www.perforce.com/perforce/doc.current/manuals/p4-git-fusion-admin/04_configuration.html#1042194>.
