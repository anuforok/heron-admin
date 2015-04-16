r'''traincheck -- check human subjects training records via CITI

Usage:
  traincheck [options] IDVAULT_NAME
  traincheck [options] --refresh --user=NAME
  traincheck [options] backfill --full=FILE --refresher=FILE --in-person=FILE

Options:
  --dbrd=NAME        environment variable with sqlalchemy URL of account
                     with read access to PII DB
                     [default: HSR_TRAIN_CHECK]
  --dbadmin=NAME     environment variable with sqlalchemy URL of account
                     with admin (create, delete, ...) access to PII DB
                     [default: HSR_TRAIN_ADMIN]
  --wsdl=URL         Service Description URL
                     [default: https://webservices.citiprogram.org/SOAP/CITISOAPService.asmx?WSDL]  # noqa
  --user=NAME        username for login to CitiSOAPService
  --pwenv=K          environment variable to look up password
                     [default: CITI_PASSWORD]
  --debug            turn on debug logging


PII DB is a database suitable for PII (personally identifiable information).

.. note:: Usage doc stops here.


Scenario one::

    >>> from sys import stdout
    >>> s1 = Mock()

Let's refresh the cache from the CITI service::

    >>> main(stdout, s1.cli_access('traincheck --refresh --user=MySchool'))

The cache is stored in the database::

    >>> s1._db.execute('select count(*) from CRS').fetchall()
    [(5,)]

Now let's look up Bob's training::

    >>> main(stdout, s1.cli_access('traincheck bob'))
    ... # doctest: +NORMALIZE_WHITESPACE
    (None, 123, None, None, u'bob', None, None, None, None,
     u'Human Subjects Research', None, None, None, None,
     None, None, None, None, 96, None, None)

But there's no training on file for Fred::

    >>> main(stdout, s1.cli_access('traincheck fred'))
    Traceback (most recent call last):
      ...
    SystemExit: no training records for fred


    >>> 'TODO: check for expired training'
    ''


Scenario two: backfill chalk records.

    >>> s2 = Mock()
    >>> main(stdout, s2.cli_access(
    ...     'traincheck backfill '
    ...          '--full=f.csv --refresher=r.csv --in-person=i.csv'))

    >>> a = s2._db.execute('select * from HumanSubjectsRefresher')
    >>> [zip(a.keys(), r) for r in a.fetchall()]
    ... # doctest: +NORMALIZE_WHITESPACE
    [[(u'FirstName', u'R3'), (u'LastName', u'S'),
      (u'Email', u'RS3@example'), (u'EmployeeID', u'J1'),
      (u'CompleteDate', u'2013-08-04 00:00:00.000000'),
      (u'Username', u'rs3')]]

'''

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

from docopt import docopt
from sqlalchemy import (MetaData, Table, Column,
                        String, Integer, Date, DateTime,
                        and_)
from sqlalchemy.engine.url import make_url

from lalib import maker
import relation

VARCHAR120 = String(120)
log = logging.getLogger(__name__)

CITI_NAMESPACE = 'https://webservices.citiprogram.org/'


def main(stdout, access):
    cli = access()

    if cli.refresh:
        svc = CitiSOAPService(cli.soapClient(), cli.auth)

        admin = TrainingRecordsAdmin(cli.account('--dbadmin'))
        for k in [
                # smallest to largest typical payload
                svc.GetGradeBooksXML,
                svc.GetMembersXML,
                svc.GetCompletionReportsXML]:
            doc = svc.get(k)
            try:
                admin.putDoc(doc)
            except StopIteration:
                raise SystemExit('no records in %s' % k)
    elif cli.backfill:
        admin = TrainingRecordsAdmin(cli.account('--dbadmin'))
        for (opt, table_name, date_style) in Chalk.tables:
            data = cli.getRecords(opt)
            admin.put(table_name, Chalk.parse_dates(data, date_style))
    else:
        store = TrainingRecordsRd(cli.account('--dbrd'))
        try:
            training = store[cli.IDVAULT_NAME]
        except KeyError:
            raise SystemExit('no training records for %s' % cli.IDVAULT_NAME)
        log.info('training OK: %s', training)
        stdout.write(str(training))


class CRS(object):
    # TODO: parse dates to avoid ...
    # Warning: Data truncated for column 'AddedMember' at row 34
    # or suppress the warning
    # Dates are actually of the form:
    # 2014-05-06T19:15:48.2-04:00
    markup = '''
      <CRS>
        <CR_InstitutionID>12345</CR_InstitutionID>
        <MemberID>12345</MemberID>
        <EmplID />
        <StudentID>12345</StudentID>
        <InstitutionUserName>a</InstitutionUserName>
        <FirstName>a</FirstName>
        <LastName>a</LastName>
        <memberEmail>a</memberEmail>
        <AddedMember>2014-05-06</AddedMember>
        <strCompletionReport>a</strCompletionReport>
        <intGroupID>12345</intGroupID>
        <strGroup>a</strGroup>
        <intStageID>12345</intStageID>
        <intStageNumber>12345</intStageNumber>
        <strStage>a</strStage>
        <intCompletionReportID>12345</intCompletionReportID>
        <intMemberStageID>12345</intMemberStageID>
        <dtePassed>2014-05-06</dtePassed>
        <intScore>12345</intScore>
        <intPassingScore>12345</intPassingScore>
        <dteExpiration>2014-05-06</dteExpiration>
      </CRS>
    '''


class GRADEBOOK(object):
    markup = '''
      <GRADEBOOK>
        <intCompletionReportID>12345</intCompletionReportID>
        <intInstitutionID>12345</intInstitutionID>
        <strCompletionReport>a</strCompletionReport>
        <intGroupID>12345</intGroupID>
        <strGroup>a</strGroup>
        <intStageID>12345</intStageID>
        <strStage>a</strStage>
      </GRADEBOOK>
    '''


class MEMBERS(object):
    # TODO: dteXXX fields are actually dates in 09/09/14 format.
    markup = '''
      <MEMBERS>
        <intMemberID>12345</intMemberID>
        <strLastII>a</strLastII>
        <strFirstII>a</strFirstII>
        <strUsernameII>a</strUsernameII>
        <strInstUsername>a</strInstUsername>
        <strInstEmail>a</strInstEmail>
        <dteAdded>a</dteAdded>
        <dteAffiliated>a</dteAffiliated>
        <dteLastLogin>a</dteLastLogin>
        <strCustom1 />
        <strCustom2 />
        <strCustom3 />
        <strCustom4 />
        <strCustom5 />
        <strSSOCustomAttrib1 />
        <strSSOCustomAttrib2>a</strSSOCustomAttrib2>
        <strEmployeeNum />
      </MEMBERS>
    '''


class HSR(object):
    def __init__(self, db_name):
        self.db_name = db_name
        log.info('HSR DB name: %s', db_name)

        ty = lambda text: (
            Integer if text == '12345' else
            # For unit testing, avoid:
            # SQLite Date type only accepts Python date objects as input.
            # by just using string.
            Date() if db_name and text == '2014-05-06' else
            VARCHAR120)

        columns = lambda markup: [
            Column(field.tag, ty(field.text))
            for field in ET.fromstring(markup)]

        meta = MetaData()

        for cls in [CRS, MEMBERS, GRADEBOOK]:
            Table(cls.__name__, meta, *columns(cls.markup),
                  schema=db_name)

        for _, name, date_col in Chalk.tables:
            Chalk.table(meta, db_name, name, date_col)

        self.tables = meta.tables

    def table(self, name):
        qname = ('%s.%s' % (self.db_name, name) if self.db_name
                 else name)
        return self.tables[qname]


class Chalk(object):
    '''Chalk back-fill data
    '''

    tables = [('--full', 'HumanSubjectsFull', 'DateCompleted'),
              ('--refresher', 'HumanSubjectsRefresher', 'CompleteDate'),
              ('--in-person', 'HumanSubjectsInPerson', 'CompleteDate')]

    @classmethod
    def table(cls, meta, db_name, name, date_col):
        return Table(name, meta,
                     Column('FirstName', VARCHAR120),
                     Column('LastName', VARCHAR120),
                     Column('Email', VARCHAR120),
                     Column('EmployeeID', VARCHAR120),
                     Column(date_col, DateTime()),
                     Column('Username', VARCHAR120),
                     schema=db_name)

    @classmethod
    def mdy(cls, txt):
        '''
        >>> Chalk.mdy('2/4/2010 0:00')
        datetime.datetime(2010, 2, 4, 0, 0)
        '''
        return datetime.strptime(txt, '%m/%d/%Y %H:%M')

    @classmethod
    def parse_dates(cls, records, date_col):
        '''
        >>> with Mock().openf('i.csv') as infp:
        ...     records = relation.readRecords(infp)
        >>> Chalk.parse_dates(records, 'CompleteDate')
        ... # doctest: +NORMALIZE_WHITESPACE
        [R(FirstName='R2', LastName='S', Email='RS2@example', EmployeeID='J1',
         CompleteDate=datetime.datetime(2012, 8, 4, 0, 0), Username='rs2')]
        '''
        fix = lambda r: r._replace(**{date_col: cls.mdy(getattr(r, date_col))})
        return [fix(r) for r in records]


@maker
def TrainingRecordsRd(
        acct,
        course='Human Subjects Research'):
    '''
    >>> inert = TrainingRecordsRd(acct=(None, None))
    >>> inert.course
    'TODO: double-check course name'

    '''

    dbtrx, db_name = acct
    crs = HSR(db_name).table('CRS')

    def __getitem__(_, instUserName):
        with dbtrx() as q:
            result = q.execute(crs.select().where(
                and_(crs.c.strCompletionReport == course,
                     crs.c.InstitutionUserName == instUserName)))
            record = result.fetchone()

        if not record:
            raise KeyError(instUserName)

        return record

    return [__getitem__], dict(course=course)


@maker
def TrainingRecordsAdmin(acct,
                         colSize=120):
    dbtrx, db_name = acct
    hsr = HSR(db_name)

    def putDoc(_, doc):
        name = iter(doc).next().tag
        tdef = hsr.table(name)
        records = relation.docToRecords(doc, [c.name for c in tdef.columns])
        put(_, name, records)

    def put(_, name, records):
        tdef = hsr.table(name)
        records = [t._asdict() for t in records]
        with dbtrx() as dml:
            log.info('(re-)creating %s', tdef.name)
            tdef.drop(dml, checkfirst=True)
            tdef.create(dml)
            dml.execute(tdef.insert(), records)
            log.info('inserted %d rows into %s', len(records), tdef.name)

    return [put, putDoc], {}


@maker
def CitiSOAPService(client, auth):
    '''CitiSOAPService

    ref https://webservices.citiprogram.org/SOAP/CITISOAPService.asmx
    '''
    methods = dict(
        GetCompletionReportsXML=client.GetCompletionReportsXML,
        GetGradeBooksXML=client.GetGradeBooksXML,
        GetMembersXML=client.GetMembersXML)

    def get(_, which):
        reply = auth(methods[which])
        markup = reply[which + 'Result']
        log.info('got length=%d from %s', len(markup), which)
        return ET.fromstring(markup.encode('utf-8'))

    attrs = dict((name, name) for name in methods.keys())
    return [get], attrs


@maker
def CLI(argv, environ, openf, create_engine, SoapClient):
    usage = __doc__.split('\n..')[0]
    opts = docopt(usage, argv=argv[1:])
    log.debug('docopt: %s', opts)

    def getBytes(_, opt):
        with openf(opts[opt]) as infp:
            return infp.read()

    def getRecords(_, opt):
        with openf(opts[opt]) as infp:
            return relation.readRecords(infp)

    def account(_, opt):
        env_key = opts[opt]
        u = make_url(environ[env_key])
        return lambda: create_engine(u).connect(), u.database

    def auth(_, wrapped):
        usr = opts['--user']
        pwd = environ[opts['--pwenv']]
        return wrapped(usr=usr, pwd=pwd)

    def soapClient(_):
        wsdl = opts['--wsdl']
        log.info('getting SOAP client for %s', wsdl)
        client = SoapClient(wsdl=wsdl)
        return client

    attrs = dict((name.replace('--', ''), val)
                 for (name, val) in opts.iteritems())
    return [getBytes, getRecords, auth, soapClient, account], attrs


class Mock(object):
    environ = dict(CITI_PASSWORD='sekret',
                   HSR_TRAIN_CHECK='sqlite://',
                   HSR_TRAIN_ADMIN='sqlite://')

    files = {
        'f.csv': (None, '''
FirstName,LastName,Email,EmployeeID,DateCompleted,Username
R,S,RS@example,J1,8/4/2011 0:00,rs
        '''.strip()),
        'i.csv': (None, '''
FirstName,LastName,Email,EmployeeID,CompleteDate,Username
R2,S,RS2@example,J1,8/4/2012 0:00,rs2
        '''.strip()),
        'r.csv': (None, '''
FirstName,LastName,Email,EmployeeID,CompleteDate,Username
R3,S,RS3@example,J1,8/4/2013 0:00,rs3
        '''.strip())}

    def __init__(self):
        import StringIO

        self._db = None  # set in cli_access()
        self.argv = []
        self._fs = dict(self.files)
        self.create_engine = lambda path: self._db
        self.SoapClient = lambda wsdl: self
        self.stdout = StringIO.StringIO()

    from contextlib import contextmanager

    @contextmanager
    def openf(self, path, mode='r'):
        import StringIO

        if mode == 'w':
            buf = StringIO.StringIO()
            self._fs[path] = (buf, None)
            try:
                yield buf
            finally:
                self._fs[path] = (None, buf.getvalue())
        else:
            _buf, content = self._fs[path]
            yield StringIO.StringIO(content)

    def cli_access(self, cmd):
        import pkg_resources as pkg
        from sqlalchemy import create_engine  # sqlite in-memory use only

        self.argv = cmd.split()

        # self._db = db = create_engine('sqlite:///mock.db')
        self._db = db = create_engine('sqlite://')
        if not '--refresh' in self.argv:
            cn = db.connect().connection
            cn.executescript(pkg.resource_string(__name__, 'test_cache.sql'))

        return lambda: CLI(self.argv, self.environ, self.openf,
                           self.create_engine, self.SoapClient)

    def _check(self, pwd):
        if not pwd == self.environ['CITI_PASSWORD']:
            raise IOError

    @classmethod
    def xml_records(self, template, qty):
        from datetime import date

        n = [10]

        def num():
            n[0] += 17
            return n[0]

        def dt():
            n[0] += 29
            return date(2000, n[0] % 12 + 1, n[0] * 3 % 27)

        def txt():
            n[0] += 13
            return 's' * (n[0] % 5) + 't' * (n[0] % 7)

        def record_markup():
            record = ET.fromstring(template)
            for field in record:
                if field.text == '12345':
                    field.text = str(num())
                elif field.text == '2014-05-06':
                    field.text = str(dt())
                else:
                    field.text = txt()

            return ET.tostring(record)

        return ("<NewDataSet>"
                + '\n'.join(record_markup()
                            for _ in range(qty))
                + "</NewDataSet>")

    def GetCompletionReportsXML(self, usr, pwd):
        self._check(pwd)
        xml = self.xml_records(CRS.markup, 5)
        return dict(GetCompletionReportsXMLResult=xml)

    def GetGradeBooksXML(self, usr, pwd):
        self._check(pwd)
        xml = self.xml_records(GRADEBOOK.markup, 3)
        return dict(GetGradeBooksXMLResult=xml)

    def GetMembersXML(self, usr, pwd):
        self._check(pwd)
        xml = self.xml_records(MEMBERS.markup, 4)
        return dict(GetMembersXMLResult=xml)


if __name__ == '__main__':
    def _privileged_main():
        from __builtin__ import open as openf
        from os import environ
        from sys import argv, stdout

        from sqlalchemy import create_engine

        def access():
            logging.basicConfig(
                level=logging.DEBUG if '--debug' in argv else logging.INFO)

            # ew... after this import, basicConfig doesn't work
            from pysimplesoap.client import SoapClient

            return CLI(argv, environ, openf,
                       create_engine, SoapClient=SoapClient)

        main(stdout, access)

    _privileged_main()
