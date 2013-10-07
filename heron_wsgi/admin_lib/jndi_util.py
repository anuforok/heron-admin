'''jndi_util -- just enough jboss JNDI to get an Oracle connection.

.. todo:: consider factoring out of rgate/i2b2hive.py
'''

from xml.etree import cElementTree as xml

from ocap_file import Readable


class JBossContext(object):
    '''
    >>> import pkg_resources as pkg
    >>> here = Readable('/example/',
    ...                 _MockPath,
    ...                 lambda path: ['test-ds.xml'],
    ...                 lambda n: pkg.resource_stream(__name__, 'test-ds.xml'))

    >>> JBossContext(here, lambda url: url).lookup('QueryToolBLUEHERONDS')
    'oracle://BLUEHERONdata:xyzpdq@bmidev1:1521/bmid'
    '''
    def __init__(self, jboss_deploy, create_engine):
        self.__d = jboss_deploy
        self.__create_engine = create_engine

    def lookup(self, n):
        url = 'oracle://%s:%s@%s:%s/%s' % ds_access(self.__d, n)
        return self.__create_engine(url)


class _MockPath(object):
    @staticmethod
    def abspath(p):
        return p

    @staticmethod
    def join(*pn):
        return '/'.join(pn)


def ds_access(jboss_deploy, jndi_name):
    '''Parse connection details of a jboss datasource by jndi-name.

    :param jboss_deploy: a read-capability to a jboss deploy directory.

    >>> import os
    >>> here_path = os.path.dirname(__file__)
    >>> here = Readable(here_path, os.path, os.listdir, open)

    >>> ds_access(here, 'QueryToolBLUEHERONDS')
    ('BLUEHERONdata', 'xyzpdq', 'bmidev1', '1521', 'bmid')

    Note case sensitivity:

    >>> ds_access(here, 'QueryToolBlueHeronDS')
    Traceback (most recent call last):
      ...
    KeyError: 'QueryToolBlueHeronDS'

    >>> ds_access(here.subRdFile('does_not_exist'), 'BLUEHERONdata')
    ... # doctest: +ELLIPSIS
    Traceback (most recent call last):
      ...
    OSError: [Errno 2] No such file or directory: ...

    :raises: XMLSyntaxError on failure to parse XML files therein,
    '''
    for f in jboss_deploy.subRdFiles():
        if not f.fullPath().endswith('-ds.xml'):
            continue
        doc = xml.parse(f.inChannel())
        srcs = doc.getroot().findall('local-tx-datasource')
        try:
            src = [src for src in srcs
             if src.find('jndi-name').text == jndi_name][0]
            un = src.find('user-name').text
            pw = src.find('password').text
            url = src.find('connection-url').text
            host, port, sid = url.split('@', 1)[1].split(':', 2)
            return un, pw, host, port, sid
        except IndexError:
            pass

    raise KeyError(jndi_name)


_token_usage = Readable
