'''medcenter.py -- academic medical center directory/policy
'''

import os
import sys
import urllib
import urllib2

import ldap # http://www.python-ldap.org/doc/html/ldap.html

import config

# consider using snake-guice for testability
# http://code.google.com/p/snake-guice/wiki/GuiceBasics

class MedCenter(object):
    def __init__(self, searchfn, trainingfn):
        self._search = searchfn
        self._training = trainingfn

    def affiliate(self, name):
        matches = self._search('(cn=%s)' % name,
                               ['cn','sn','givenname','mail'])
        if len(matches) != 1:
            if len(matches) == 0:
                raise KeyError, name
            else:
                raise ValueError, name  # ambiguous

        dn, x = matches[0]

        return AccountHolder(x)

    def trainedThru(self, who):
        return self._training(who.userid())


class AccountHolder(object):
    def __init__(self, attrs):
        self._userid = attrs['cn'][0]
        self._sn = attrs['sn'][0]
        self._givenname = attrs['givenname'][0]
        self._mail = attrs['mail'][0]

    def __str__(self):
        return '%s, %s <%s>' % (self._sn, self._givenname, self._mail)

    def userid(self):
        # TODO: use python property stuff?
        return self._userid


def ldap_searchfn(ini, section):
    rt = config.RuntimeOptions('url userdn base password')
    rt.load(ini, section)
    l = ldap.initialize(rt.url)
    # TODO: figure out how to configure openssl to handle self-signed certs
    # work-around: use `TLS_REQCERT allow` in /etc/ldap/ldap.conf
    # (not to be confused with /etc/ldap.conf)
    l.simple_bind_s(rt.userdn, rt.password)
    base = rt.base

    def search(query, attrs):
        return l.search_s(base, ldap.SCOPE_SUBTREE, query, attrs)

    return search

def chalkdb_queryfn(ini, section):
    rt = config.RuntimeOptions('url param')

    def training_expiration(userid):
        addr = rt.url + '?' + urllib.urlencode({rt.param: userid})
        body = urllib2.urlopen(addr).read()

        if not body:  # no expiration on file
            raise KeyError

        return body.strip()  # get rid of newline
    return training_expiration


def _integration_test(ldap_ini='kumc-idv.ini', ldap_section='idvault',
                      chalk_ini='chalk.ini', chalk_section='chalk'):

    ls = ldap_searchfn(ldap_ini, ldap_section)
    cq = chalkdb_queryfn(chalk_ini, chalk_section)

    return MedCenter(ls, cq)



if __name__ == '__main__':
    uid = sys.argv[1]
    m = _integration_test()
    who = m.affiliate(uid)
    print who
    print "training: ", m.trainedThru(who)
