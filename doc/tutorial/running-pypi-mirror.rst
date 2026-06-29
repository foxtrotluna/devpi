
==========================================================
Running a pypi mirror
==========================================================

This document let's you quickly run and manage ``devpi-server``
for serving an efficient self-updating PyPI caching mirror,
suitable for offline operations after an initial cache fill.


Set up devpi server
++++++++++++++++++++++++++++++++++++++++++++++
Before running a pypi mirror, ensure you have set up a devpi server as detailed in :doc:`installing-server`
with a server listening on ``http://localhost:3141``.

.. _`install_first`:

Install your first package with pip/easy_install
+++++++++++++++++++++++++++++++++++++++++++++++++++++

Both pip_ and easy_install_ support the ``-i`` option to specify
an index server url.  We use it to point installers to a special
``root/pypi`` index, served by ``devpi-server`` by default.
Let's install the ``pg8000`` package as a test from our cache::

    $ pip install -i http://localhost:3141/root/pypi/+simple/ pg8000==1.30.2 scramp==1.4.4 python-dateutil==2.8.2 six==1.16.0 asn1crypto==1.5.1
    Looking in indexes: http://localhost:3141/root/pypi/+simple/
    Collecting pg8000==1.30.2
      Downloading pg8000-1.30.2-py3-none-any.whl.metadata (78 kB)
    Collecting scramp==1.4.4
      Downloading scramp-1.4.4-py3-none-any.whl.metadata (19 kB)
    Collecting python-dateutil==2.8.2
      Downloading python_dateutil-2.8.2-py2.py3-none-any.whl.metadata (8.2 kB)
    Collecting six==1.16.0
      Downloading six-1.16.0-py2.py3-none-any.whl.metadata (1.8 kB)
    Collecting asn1crypto==1.5.1
      Downloading asn1crypto-1.5.1-py2.py3-none-any.whl.metadata (13 kB)
    Downloading pg8000-1.30.2-py3-none-any.whl (54 kB)
    Downloading scramp-1.4.4-py3-none-any.whl (13 kB)
    Downloading python_dateutil-2.8.2-py2.py3-none-any.whl (247 kB)
    Downloading six-1.16.0-py2.py3-none-any.whl (11 kB)
    Downloading asn1crypto-1.5.1-py2.py3-none-any.whl (105 kB)
    Installing collected packages: asn1crypto, six, scramp, python-dateutil, pg8000
    Successfully installed asn1crypto-1.5.1 pg8000-1.30.2 python-dateutil-2.8.2 scramp-1.4.4 six-1.16.0

Feel free to install any other package.  If you encounter lookup/download
issues when installing a public pypi package, please report the offending
package name to the `devpi issue tracker`_, at best including
the output of ``devpi-server --log``.  We constantly aim to get the
mirroring 100% bug free and compatible to pypi.org.

.. _perminstallindex:

Permanent index configuration for pip
+++++++++++++++++++++++++++++++++++++++++++++++++++++

To avoid having to re-type index URLs with ``pip`` or ``easy-install`` ,
you can configure pip by setting the index-url entry in your
``$HOME/.pip/pip.conf`` (posix) or ``$HOME/pip/pip.ini`` (windows).
Let's do it for the ``root/pypi`` index::

    # $HOME/.pip/pip.conf
    [global]
    index-url = http://localhost:3141/root/pypi/+simple/

Alternatively, you can add a special environment variable
to your shell settings (e.g. ``.bashrc``):

   export PIP_INDEX_URL=http://localhost:3141/root/pypi/+simple/

For ``pip search`` you need a ``[search]`` section in your ``pip.conf``::

    # $HOME/.pip/pip.conf
    [global]
    index-url = http://localhost:3141/root/pypi/+simple/

    [search]
    index = http://localhost:3141/root/pypi/


Permanent index configuration for easy_install
+++++++++++++++++++++++++++++++++++++++++++++++++++++++++

You can configure ``easy_install`` by an entry in
the ``$HOME/.pydistutils.cfg`` file::

    # $HOME/.pydistutils.cfg:
    [easy_install]
    index_url = http://localhost:3141/root/pypi/+simple/
