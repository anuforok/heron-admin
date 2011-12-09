r'''cas_auth - JA-SIG Central Authentication Service (CAS_) support

.. _CAS: http://www.jasig.org/cas

Suppose we have a pyramid view that we want protected by CAS::
  >>> from pyramid.response import Response
  >>> def protected_view(context, req):
  ...     log.debug('protected view: %s', ['I am: ', req.remote_user])
  ...     return Response(app_iter=['I am: ', req.remote_user])

Let's set up authorization and authentication::
  >>> from pyramid.config import Configurator
  >>> config = Configurator()

  >>> depgraph = Mock.depgraph()

  >>> rt = depgraph.get((Options, CONFIG_SECTION))
  >>> rt.base
  'http://example/cas/'

  >>> guide = depgraph.get(Issuer)
  >>> guard = depgraph.get(Validator)
  >>> guard.add_issuer(guide)

  >>> config.add_route('logout', 'logout')
  >>> guard.configure(config, 'logout')
  >>> config.set_authorization_policy(CapabilityStyle([guide]))

  >>> config.add_route('root', '')
  >>> config.add_view(protected_view, route_name='root',
  ...                 permission=guide.permissions[0])

An initial visit redirects to the CAS service with the `service` param
set to our login address::

  >>> from paste.fixture import TestApp
  >>> t = TestApp(config.make_wsgi_app())
  >>> r1 = t.get('/', status=303)
  >>> def _loc(headers):
  ...    return [v for (n, v) in headers if n.lower() == 'location'][0]
  >>> _loc(r1.headers)
  'http://example/cas/login?service=http%3A%2F%2Flocalhost%2F'

The the CAS service redirects back with a ticket::

  >>> r3 = t.get('/?ticket=ST-381409-fsFVbSPrkoD9nANruV4B-example',
  ...            status=302)
  >>> _loc(r3.headers)
  'http://localhost/'

Now, our protected app runs inside an `auth_tkt` session::

  >>> r4 = r3.follow(status=200)
  >>> r4
  <Response 200 OK 'I am: john.smith'>
  >>> [v.split('=', 1)[0] for (n, v) in r3.headers
  ...  if n.lower() == 'set-cookie']
  ['auth_tkt', 'auth_tkt']

Then, more than 10 minutes later, the session has timed out,
so we should get a challenge on the next request::

  >>> from pyramid.authentication import AuthTktCookieHelper
  >>> import time
  >>> AuthTktCookieHelper.now = time.time() + 40 * 60

  >>> _loc(t.get(r4.request.url, status=303).headers)
  'http://example/cas/login?service=http%3A%2F%2Flocalhost%2F'

Finally, log in again and log out, and then get a challenge::

  >>> rlogin2 = t.get('/?ticket=ST-381409-fsFVbSPrkoD9nANruV4B-example',
  ...                 status=302)
  >>> rlogout = t.post('/logout', status=303)
  >>> _loc(rlogout.headers)
  'http://example/cas/logout'

  >>> r0 = t.get('/', status=303)
  >>> _loc(r0.headers)
  'http://example/cas/login?service=http%3A%2F%2Flocalhost%2F'
'''

# python stdlib 1st, per PEP8
import itertools
import logging
import urllib
import urllib2

# from pypi
import injector
from injector import inject, provides

import pyramid
from pyramid import security
from pyramid.httpexceptions import HTTPForbidden, HTTPFound, HTTPSeeOther
from pyramid.authentication import AuthTktAuthenticationPolicy

from admin_lib.rtconfig import Options, TestTimeOptions, RuntimeOptions
from admin_lib import sealing

log = logging.getLogger(__name__)

CONFIG_SECTION='cas'

class Issuer(object):
    '''Issuer of capabilities based on CAS login credentials.

    See MockIssuer for an example.
    '''
    permissions = ()

    def issue(self, req):
        '''Issue capabilities based on CAS login.

        @param req: request, bearing CAS userid in remote_user,
                    which you may add capability attributes to
        @return: a sequence of authorizations for this capabilities
                 added to this request, which will later be passed to
                 audit in case this issuer's permission is required.
        '''
        raise NotImplemented

    def audit(self, cap, permission):
        '''Test whether cap authorizes permission.

        @param cap: any object, which should be tested to see if it is
                    an authorization we issued.
        @permission: one of this issuer's permissions
        '''
        raise NotImplemented


class Validator(object):
    def __init__(self, cas_addr, app_secret, ua=None):
        if ua is None:
            ua = urllib2.build_opener()
        self._ua = ua
        self._a = cas_addr
        # When this thing is missing from the config file,
        # the stacktrace is really obscure.
        assert app_secret
        self._secret = app_secret
        self._issuers = []

    def __str__(self):
        return 'Validator(cas_addr=%s)' % self._a

    def policy(self):
        return AuthTktAuthenticationPolicy(
            self._secret, callback=self.caps,
            timeout=10 * 60,
            reissue_time=1 * 60,
            wild_domain=False)

    def configure(self, config, logout_route):
        pwho = self.policy()
        config.set_authentication_policy(pwho)

        config.add_subscriber(self.check, pyramid.events.NewRequest)

        config.add_view(self.redirect,
                        context=pyramid.exceptions.HTTPForbidden,
                        permission=pyramid.security.NO_PERMISSION_REQUIRED)
        config.add_view(self.logout, route_name=logout_route,
                        request_method='POST')

    def redirect(self, context, request):
        if 'ticket' in request.params:
            # already been here before
            return HTTPForbidden()

        log.debug("redirector from: %s", request.url)
        there = (urllib.basejoin(self._a, 'login') + '?' +
                 urllib.urlencode(dict(service=request.url)))
        log.debug("redirector to: %s, %s, %s", there, self._a, request.url)
        return HTTPSeeOther(there)

    def check(self, event):
        req = event.request

        log.debug('check %s', req.url)

        t = req.GET.get('ticket')
        if not t:
            log.debug('no ticket arg; redirect to CAS service')
            return None  # or: raise HTTPBadRequest()

        a = self._a + 'validate?' + urllib.urlencode(dict(service=req.path_url,
                                                          ticket=t))
        log.debug('cas validation request: %s', a)
        lines = self._ua.open(a).read().split('\n')

        log.info('cas validation result: %s', lines)

        if not(lines and lines[0] == 'yes'):
            return None  # or: raise HTTPForbidden()

        uid = lines[1].strip()

        hdrs = security.remember(req, uid)
        log.debug("new headers: %s", hdrs)

        response = HTTPFound(req.path_url)
        response.headers.extend(hdrs)
        raise response

    def add_issuer(self, issuer):
        self._issuers.append(issuer)

    def caps(self, uid, req):
        log.debug('issuing CAS login capabilities for: %s', uid)
        # 1st capability is the user id itself
        req.remote_user = uid
        return _flatten([issuer.issue(req)
                         for issuer in self._issuers])
        
    def logout(self, context, req):
        response = HTTPSeeOther(urllib.basejoin(self._a, 'logout'))
        response.headers.extend(security.forget(req))
        raise response


def _flatten(listoflists):
    return list(itertools.chain(*listoflists))

class CapabilityStyle(object):
    def __init__(self, auditors):
        self._auditors = auditors

    def permits(self, context, principals, permission):
        log.debug('CapabilityStyle.permits? %s %s %s',
                  context, principals, permission)

        for auditor in self._auditors:
            if permission in auditor.permissions:
                for cap in principals:
                    try:
                        # maybe pass all principals to audit in one go?
                        auditor.audit(cap, permission)
                        return True
                    except TypeError:
                        pass

        log.debug('permits: False')
        return False

    def principals_allowed_by_permission(self, context, permission):
        raise NotImplementedError


class SetUp(injector.Module):
    @provides(Validator)
    @inject(rt=(Options, CONFIG_SECTION),
            ua=urllib.URLopener)
    def validator(self, rt, ua):
        return Validator(rt.base, rt.app_secret, ua)
        

class RunTime(injector.Module):
    def __init__(self, ini):
        self._ini = ini

    def configure(self, binder):
        binder.bind((Options, CONFIG_SECTION),
                    RuntimeOptions(['base', 'app_secret']
                                   ).load(self._ini, 'cas'))

        binder.bind(urllib.URLopener,
                    to=injector.InstanceProvider(urllib2.build_opener()))

    @classmethod
    def mods(cls, ini):
        return [SetUp(), RunTime(ini)]

    @classmethod
    def depgraph(cls, ini):
        return injector.Injector(cls.mods(ini))


class LinesUrlOpener(object):
    '''An URL opener to help with CAS testing
    '''
    def __init__(self, lines):
        self._lines = lines

    def open(self, addr, body=None):
        return LinesResponse(self._lines)

class LinesResponse(object):
    def __init__(self, lines):
        self._lines = lines

    def read(self):
        return '\n'.join(self._lines)


class Mock(injector.Module):
    def configure(self, binder):
        binder.bind((Options, CONFIG_SECTION),
                    to=TestTimeOptions({'base': 'http://example/cas/',
                                        'app_secret': 'sekrit'}))
        binder.bind(urllib.URLopener,
                    to=injector.InstanceProvider(LinesUrlOpener(
                    ['yes', 'john.smith'])))

        binder.bind(Issuer, MockIssuer)

    @classmethod
    def mods(cls):
        return [SetUp(), Mock()]

    @classmethod
    def depgraph(cls):
        return injector.Injector(cls.mods())


class MockIssuer(object):
    permissions = ('treasure',)

    def __init__(self):
        self.sealer, self._unsealer = sealing.makeBrandPair('treasure')

    def issue(self, req):
        cap = 'Canary Islands'
        req.treasure_location = cap
        return [self.sealer.seal(cap)]

    def audit(self, cap, permission):
        try:
            self._unsealer.unseal(cap)
        except AttributeError:  # e.g. in case of string principals
            raise TypeError


def _integration_test(ini, host='127.0.0.1', port=8123):
    from pyramid.config import Configurator
    from pyramid.response import Response
    from paste import httpserver

    logging.basicConfig(level=logging.DEBUG)

    def protected_view(req):
        return Response(app_iter=['I am: ', req.remote_user])

    config = Configurator(settings={'pyramid.debug_routematch': True})

    depgraph = RunTime.depgraph(ini)
    guide = MockIssuer()
    guard = depgraph.get(Validator)
    guard.add_issuer(guide)
    config.add_route('logout', 'logout')
    guard.configure(config, 'logout')

    pwhat = CapabilityStyle([guide])
    config.set_authorization_policy(pwhat)

    config.add_route('root', '')
    config.add_view(protected_view, route_name='root',
                    permission=guide.permissions[0])

    app = config.make_wsgi_app()
    httpserver.serve(app, host, port)


if __name__ == '__main__':
    import sys
    ini = sys.argv[1]
    _integration_test(ini)
