import hashlib
from contextlib import closing
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import build_opener, HTTPCookieProcessor, Request

import bcrypt
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AdminUser, Store, CustomerProductType, CustomerFormField, CreStaffMember, CustomerVisit
from migrate_from_main import migrate
ROOT=Path(__file__).resolve().parents[1]
PASSWORD='Standalone-test-password'

def fixture(path):
    engine=create_engine('sqlite:///'+Path(path).as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Store(id=1,store_name='Test Store',city='Indore',store_address='Test address',timings='10-6'))
        db.commit()
        hashed=bcrypt.hashpw(PASSWORD.encode(),bcrypt.gensalt()).decode()
        db.add_all([AdminUser(id=1,username='owner',role='super_admin',password_hash=hashed),
                    AdminUser(id=2,username='historical',role='contact_manager',password_hash=hashed),
                    AdminUser(id=3,username='unrelated',role='store_admin',password_hash=hashed),
                    AdminUser(id=4,username='executive',role='customer_relation_executive',password_hash=hashed)])
        db.commit()
        db.add_all([CustomerProductType(id=1,name='Ring',created_by=1),
                    CustomerFormField(id=1,label='Budget',field_type='number',is_required=1,created_by=1),
                    CreStaffMember(id=1,name='Sales Test',staff_type='sales_person',created_by=2),
                    CreStaffMember(id=2,name='Manager Test',staff_type='manager',created_by=1)])
        db.commit()
        db.add(CustomerVisit(id=1,customer_name='Initial',mobile_number='9876543210',product_type_id=1,
                             handled_by_id=1,assigned_manager_id=2,store_id=1,activity_type='New Jewellery',
                             status='Draft',created_by=4,custom_fields='{"1": "5000"}'))
        db.commit()
    engine.dispose()

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'source.db'
        self.target=Path(self.tmp.name)/'cre.db'
        fixture(self.source)

    def test_consistent_wal_snapshot_and_only_cre_data(self):
        with closing(sqlite3.connect(str(self.source))) as live:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute("UPDATE customer_visits SET customer_name='Committed WAL' WHERE id=1")
            live.execute('CREATE TABLE unrelated_data (secret TEXT)')
            live.execute("INSERT INTO unrelated_data VALUES ('not CRE')")
            live.commit()
            before=hashlib.sha256(self.source.read_bytes()).hexdigest()
            report=migrate(self.source,self.target)
            self.assertEqual(before,hashlib.sha256(self.source.read_bytes()).hexdigest())
            self.assertEqual(live.execute('SELECT count(*) FROM admin_users').fetchone()[0],4)
        with closing(sqlite3.connect(str(self.target))) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables,{'stores','admin_users','customer_product_types','customer_form_fields','cre_staff_members','customer_visits','cre_visit_options'})
            self.assertEqual(db.execute('SELECT customer_name FROM customer_visits').fetchone()[0],'Committed WAL')
            self.assertEqual(db.execute('SELECT id FROM admin_users ORDER BY id').fetchall(),[(1,),(2,),(4,)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('SELECT custom_fields FROM customer_visits').fetchone()[0],'{"1": "5000"}')
        self.assertEqual(report['tables']['customer_visits'],1)
        self.assertEqual(report['integrity'],'ok')

    def test_existing_target_and_source_are_never_overwritten(self):
        migrate(self.source,self.target)
        before=self.target.read_bytes()
        with self.assertRaises(FileExistsError): migrate(self.source,self.target)
        self.assertEqual(before,self.target.read_bytes())
        with self.assertRaises(ValueError): migrate(self.source,self.source)

    def test_broken_relationship_rolls_back_without_publishing(self):
        with closing(sqlite3.connect(str(self.source))) as db, db:
            db.execute('UPDATE customer_visits SET handled_by_id=999 WHERE id=1')
        with self.assertRaises(sqlite3.IntegrityError): migrate(self.source,self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('.cre-import-*')))

    def test_unknown_schema_is_rejected_without_data_loss(self):
        with closing(sqlite3.connect(str(self.source))) as db, db:
            db.execute('ALTER TABLE customer_visits ADD COLUMN future_value TEXT')
        with self.assertRaises(ValueError): migrate(self.source,self.target)
        self.assertFalse(self.target.exists())

class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.dbpath=Path(cls.tmp.name)/'web.db'
        fixture(cls.dbpath)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            cls.port=sock.getsockname()[1]
        cls.url='http://127.0.0.1:'+str(cls.port)
        env=dict(os.environ, CRE_DATABASE_URL='sqlite:///'+cls.dbpath.as_posix(),
                 CRE_SECRET_KEY='independent-test-secret-that-is-not-used-outside-tests', PYTHONUTF8='1')
        env.pop('PYTHONPATH',None)
        cls.log=open(Path(cls.tmp.name)/'server.log','w+',encoding='utf-8')
        bootstrap = (
            "import sys,threading; sys.path.insert(0,"+repr(str(ROOT))+"); "
            "import uvicorn; server=uvicorn.Server(uvicorn.Config('server:app',host='127.0.0.1',port="+str(cls.port)+")); "
            "threading.Thread(target=lambda: (sys.stdin.buffer.read(1),setattr(server,'should_exit',True)),daemon=True).start(); server.run()"
        )
        cls.process=subprocess.Popen([sys.executable,'-B','-c',bootstrap],
                                     cwd=cls.tmp.name,env=env,stdin=subprocess.PIPE,stdout=cls.log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if cls.process.poll() is not None: raise RuntimeError('Test server exited')
                try:
                    with build_opener().open(cls.url+'/health',timeout=1) as response:
                        if response.status==200: break
                except OSError: time.sleep(.2)
            else: raise RuntimeError('Test server did not start')
        except Exception:
            cls.stop_server()
            cls.log.seek(0); detail=cls.log.read(); cls.log.close(); cls.tmp.cleanup()
            raise RuntimeError(detail)
    @classmethod
    def stop_server(cls):
        cls.process.communicate(input=b'x',timeout=10)
    @classmethod
    def tearDownClass(cls):
        cls.stop_server()
        cls.log.close(); cls.tmp.cleanup()
    def setUp(self):
        self.cookies=CookieJar()
        self.client=build_opener(HTTPCookieProcessor(self.cookies))
    def request(self,path,data=None):
        body=urlencode(data).encode() if data is not None else None
        try:
            with self.client.open(Request(self.url+path,data=body),timeout=10) as response:
                return response.status,response.read(),response.url,response.headers
        except HTTPError as error:
            return error.code,error.read(),error.url,error.headers
    def login(self,user='owner',password=PASSWORD):
        return self.request('/cre/login',{'username':user,'password':password})
    def sql(self,query,params=()):
        with closing(sqlite3.connect(str(self.dbpath))) as db:
            return db.execute(query,params).fetchall()
    def form(self,**changes):
        data=dict(customer_name='Standalone Customer',mobile_number='9876543210',product_type_id=1,
                  handled_by_id=1,assigned_manager_id=2,store_id=1,activity_type='New Jewellery',custom_1='12000')
        data.update(changes); return data
    def test_login_roles_cookie_and_logout(self):
        self.assertEqual(urlparse(self.request('/cre/dashboard')[2]).path,'/cre/login')
        self.assertEqual(self.login(password='wrong')[0],401)
        self.assertEqual(self.login(user='historical')[0],401)
        self.assertEqual(self.login(user='executive')[0],200)
        self.assertIn('anand_cre_session',{cookie.name for cookie in self.cookies})
        self.assertNotIn('session',{cookie.name for cookie in self.cookies})
        self.assertEqual(urlparse(self.request('/cre/logout')[2]).path,'/cre/login')
        self.assertEqual(urlparse(self.request('/cre/dashboard')[2]).path,'/cre/login')
    def test_optional_sales_person_and_manager(self):
        self.login()
        for index, (sales, manager) in enumerate([(0, 0), (1, 0), (0, 2), ('', '')]):
            data = self.form(customer_name='Optional staff '+str(index), status='Draft', handled_by_id=sales, assigned_manager_id=manager)
            self.assertNotIn('error=', self.request('/cre/customers', data)[2])
            visit, saved_sales, saved_manager = self.sql('SELECT id,handled_by_id,assigned_manager_id FROM customer_visits WHERE customer_name=?', (data['customer_name'],))[0]
            self.assertEqual((saved_sales, saved_manager), (sales or None, manager or None))
            self.assertNotIn('error=', self.request('/cre/customers/'+str(visit)+'/edit', dict(data, handled_by_id=1, assigned_manager_id=2))[2])
            self.assertEqual(self.sql('SELECT handled_by_id,assigned_manager_id FROM customer_visits WHERE id=?', (visit,)), [(1, 2)])
            self.assertNotIn('error=', self.request('/cre/customers/'+str(visit)+'/edit', dict(data, handled_by_id=0, assigned_manager_id=0))[2])
            self.assertEqual(self.sql('SELECT handled_by_id,assigned_manager_id FROM customer_visits WHERE id=?', (visit,)), [(None, None)])
            self.assertIn('error=', self.request('/cre/customers/'+str(visit)+'/edit', dict(data, assigned_manager_id=1))[2])
        self.assertIn('error=', self.request('/cre/customers', self.form(handled_by_id=2))[2])
        self.assertIn('error=', self.request('/cre/customers', self.form(assigned_manager_id=999))[2])
        self.assertIn(b'Not Assigned', self.request('/cre/dashboard')[1])
        self.assertIn(b'Not Assigned', self.request('/cre/reports')[1])
        engine = create_engine('sqlite:///'+self.dbpath.as_posix())
        from sqlalchemy import text
        try:
            with engine.begin() as db:
                db.execute(text('UPDATE cre_staff_members SET is_active=0'))
            self.assertIn(b'id="addCustomerModal"', self.request('/cre/dashboard')[1])
            self.assertNotIn('error=', self.request('/cre/customers', self.form(handled_by_id=0, assigned_manager_id=0))[2])
        finally:
            with engine.begin() as db:
                db.execute(text('UPDATE cre_staff_members SET is_active=1'))
            engine.dispose()

    def test_single_status_replaces_purpose(self):
        self.login()
        choices = ['Draft', 'Billed', 'Walkout', 'Old Gold', 'Repair Taken', 'Repair Delivered', 'Ordered Delivered']
        for index, choice in enumerate(choices):
            data = self.form(customer_name='Unified status '+str(index), status=choice)
            data.pop('activity_type')
            if choice == 'Walkout':
                data.update(remark='Selected another design', walkout_reason='Design', competitor_shop='Tanishq')
            self.assertNotIn('error=', self.request('/cre/customers', data)[2], choice)
            visit, activity, stored = self.sql('SELECT id,activity_type,status FROM customer_visits WHERE customer_name=?', (data['customer_name'],))[0]
            self.assertEqual((activity, stored), ('New Jewellery', choice) if choice in choices[:3] else (choice, None))
            dashboard = self.request('/cre/dashboard?activity='+quote(choice))[1]
            self.assertIn(data['customer_name'].encode(), dashboard)
            self.assertNotIn(b'<th>Purpose</th>', dashboard)
            self.assertNotIn(b'name="activity_type"', dashboard)
            edit = dict(data, status='Draft')
            self.assertNotIn('error=', self.request('/cre/customers/'+str(visit)+'/edit', edit)[2])
            self.assertEqual(self.sql('SELECT status,walkout_reason,competitor_shop FROM customer_visits WHERE id=?', (visit,)), [('Draft', None, None)])
            self.assertNotIn('error=', self.request('/cre/customers/'+str(visit)+'/status', {'status': 'Billed'})[2])
        invalid = self.form(status='Not a status'); invalid.pop('activity_type')
        self.assertIn('error=', self.request('/cre/customers', invalid)[2])
        invalid['status'] = 'Walkout'
        self.assertIn('error=', self.request('/cre/customers', invalid)[2])
        self.assertNotIn(b'<th>Purpose</th>', self.request('/cre/reports')[1])

    def test_dynamic_walkout_choices_and_history(self):
        self.assertEqual(urlparse(self.request('/cre/walkout-options')[2]).path, '/cre/login')
        self.request('/cre/walkout-options/reason', {'name': 'Unauthenticated'})
        self.assertFalse(self.sql("SELECT id FROM cre_visit_options WHERE name='Unauthenticated'"))
        self.login()
        for kind, name in [('reason', 'Delivery timeline'), ('competitor', 'New Local Jewels')]:
            self.assertNotIn('error=', self.request('/cre/walkout-options/'+kind, {'name': name})[2])
            self.assertIn(name.encode(), self.request('/cre/dashboard')[1])
            self.assertIn('error=', self.request('/cre/walkout-options/'+kind, {'name': name.upper()})[2])
        self.assertIn('error=', self.request('/cre/walkout-options/reason', {'name': 'x'*81})[2])
        self.assertIn('error=', self.request('/cre/walkout-options/reason', {'name': '  '})[2])
        self.request('/cre/customers', self.form(customer_name='Dynamic visit'))
        visit = self.sql("SELECT id FROM customer_visits WHERE customer_name='Dynamic visit'")[0][0]
        route = '/cre/customers/'+str(visit)
        payload = dict(status='Walkout', remark='Requested later delivery', walkout_reason='Delivery timeline', competitor_shop='New Local Jewels')
        self.assertNotIn('error=', self.request(route+'/status', payload)[2])
        self.assertNotIn('error=', self.request(route+'/edit', self.form(customer_name='Dynamic visit', **payload))[2])
        self.assertIn('error=', self.request(route+'/status', dict(payload, competitor_shop='Unknown shop'))[2])
        ids = self.sql("SELECT id FROM cre_visit_options WHERE name IN ('Delivery timeline','New Local Jewels')")
        for (option_id,) in ids:
            self.request('/cre/walkout-options/'+str(option_id)+'/remove', {})
        # Migration is repeatable and does not re-enable removed choices.
        from visit_options import migrate_001_visit_options
        engine = create_engine('sqlite:///'+self.dbpath.as_posix())
        try:
            migrate_001_visit_options(engine)
        finally:
            engine.dispose()
        self.assertEqual(self.sql("SELECT sum(is_active) FROM cre_visit_options WHERE name IN ('Delivery timeline','New Local Jewels')"), [(0,)])
        self.assertIn(b'Delivery timeline (removed)', self.request('/cre/dashboard')[1])
        self.assertNotIn('error=', self.request(route+'/edit', self.form(customer_name='Dynamic visit', **payload))[2])
        self.assertIn(b'Delivery timeline', self.request('/cre/reports')[1])
        self.request('/cre/customers', self.form(customer_name='Another dynamic visit'))
        another = self.sql("SELECT id FROM customer_visits WHERE customer_name='Another dynamic visit'")[0][0]
        self.assertIn('error=', self.request('/cre/customers/'+str(another)+'/status', payload)[2])
        for (option_id,) in ids:
            self.request('/cre/walkout-options/'+str(option_id)+'/restore', {})
        self.assertNotIn('error=', self.request('/cre/customers/'+str(another)+'/status', payload)[2])
        self.assertIn(b'Walkout &amp; Competitors', self.request('/cre/walkout-options')[1])
    def test_pages_and_main_admin_are_isolated(self):
        self.login()
        for path in ('/cre/dashboard','/cre/reports','/cre/sales-people','/cre/managers','/cre/products','/cre/fields','/cre/walkout-options'):
            code,body,_,_=self.request(path)
            self.assertEqual(code,200,path)
            self.assertNotIn(b'href="/admin',body)
            self.assertNotIn(b'Gold Rate Management',body)
        self.assertEqual(self.request('/static/images/logo.png')[0],200)
        self.assertEqual(self.request('/static/style.css')[0],200)
        self.assertEqual(self.request('/admin')[0],404)
        self.assertEqual(self.request('/api/gold-rates/latest')[0],404)
    def test_customer_create_edit_walkout_and_export(self):
        self.login()
        _,_,url,_=self.request('/cre/customers',self.form())
        self.assertNotIn('error=',url)
        visit=self.sql("SELECT id FROM customer_visits WHERE customer_name='Standalone Customer' ORDER BY id DESC")[0][0]
        self.assertEqual(self.sql('SELECT status,created_by,custom_fields FROM customer_visits WHERE id=?',(visit,)),[('Draft',1,'{"1": "12000"}')])
        self.request('/cre/customers/'+str(visit)+'/edit',self.form(customer_name='Edited Customer',status='Draft'))
        self.assertEqual(self.sql('SELECT customer_name FROM customer_visits WHERE id=?',(visit,))[0][0],'Edited Customer')
        self.request('/cre/customers/'+str(visit)+'/status',dict(status='Walkout',remark='Budget test',walkout_reason='Budget',competitor_shop='Tanishq'))
        self.assertEqual(self.sql('SELECT status,walkout_reason FROM customer_visits WHERE id=?',(visit,)),[('Walkout','Budget')])
        filters='?date_from=2000-01-01&date_to=2100-01-01&store_id=1'
        code,body,_,_=self.request('/cre/reports'+filters)
        self.assertEqual(code,200)
        self.assertIn(b'Budget',body)
        code,body,_,headers=self.request('/cre/reports/export/excel'+filters)
        self.assertEqual(code,200)
        workbook=load_workbook(io.BytesIO(body))
        self.assertEqual(len(workbook.sheetnames),5)
        self.assertGreaterEqual(workbook['Walk-in Summary']['B5'].value,1)
        self.assertIn('.xlsx',headers['Content-Disposition'])
        self.assertIn(b'Edited Customer',self.request('/cre/dashboard?q=Edited')[1])
    def test_required_custom_field_and_invalid_relationship_are_rejected(self):
        self.login()
        before=self.sql('SELECT count(*) FROM customer_visits')[0][0]
        self.assertIn('error=',self.request('/cre/customers',self.form(custom_1=''))[2])
        self.assertIn('error=',self.request('/cre/customers',self.form(handled_by_id=999))[2])
        self.assertEqual(self.sql('SELECT count(*) FROM customer_visits')[0][0],before)
    def test_configuration_and_field_restore(self):
        self.login()
        self.request('/cre/products',{'name':'Test Pendant'})
        self.assertTrue(self.sql("SELECT id FROM customer_product_types WHERE name='Test Pendant'"))
        self.request('/cre/sales-people',{'name':'New Sales Test'})
        self.request('/cre/managers',{'name':'New Manager Test'})
        self.assertEqual(self.sql("SELECT count(*) FROM cre_staff_members WHERE name LIKE 'New % Test'")[0][0],2)
        self.request('/cre/fields/1/remove',{})
        self.assertEqual(self.sql('SELECT is_active FROM customer_form_fields WHERE id=1')[0][0],0)
        self.request('/cre/fields/1/restore',{})
        self.assertEqual(self.sql('SELECT is_active FROM customer_form_fields WHERE id=1')[0][0],1)

if __name__=='__main__': unittest.main()
