"""Authenticated application boundary. Raw Hospital is for trusted maintenance/tests."""
import inspect
import sqlite3
from contextvars import ContextVar
from .auth import authenticate,AccessDenied
from .database import connection

actor=ContextVar('staff_actor',default=None)
PAGES={
    'admin':['Overview','Patients','Appointments','Care workspace','Wards','Doctor sessions','Agent decisions','Staff management','Evaluation & backup'],
    'front_desk':['Overview','Patients','Appointments'],
    'nurse':['Overview','Care workspace'],
    'physician':['Overview','Care workspace','Wards'],
    'pharmacy':['Care workspace'],
    'ward':['Wards'],
}
METHODS={
    'front_desk':{'create_patient','update_email','book','reschedule','close_booking','check_in','clear_billing','reminder_preference'},
    'nurse':{'record_vitals'},'physician':{'transition','admit','start_consultation'},
    'pharmacy':{'transition'},'ward':{'transition','admit'},
}
ALL_METHODS=set().union(*METHODS.values())|{'set_session','set_ward_capacity','set_availability','add_break','remove_break'}
COMMON={'patients','appointments','doctors','sessions','doctor_breaks'}
CLINICAL=COMMON|{'visits','vitals','wards','consultations'}
TABLES={'front_desk':COMMON,'nurse':CLINICAL,'physician':CLINICAL,'pharmacy':CLINICAL,'ward':CLINICAL,
        'admin':CLINICAL|{'events','decisions','notifications','legacy_imports','legacy_records','staff_audit'}}


class StaffHospital:
    def __init__(self,core,token):
        self._core=core
        self._token=token

    @property
    def path(self): return self._core.path

    @property
    def today(self): return self._core.today

    @property
    def user(self):
        user=authenticate(self.path,self._token)
        if user['must_change'] or user['role'] not in PAGES:
            raise AccessDenied('Change your temporary password first, or use the patient portal.')
        return user

    def can(self,method):
        role=self.user['role']
        return method in ALL_METHODS and (role=='admin' or method in METHODS.get(role,set()))

    def authorize_page(self,page):
        if page not in PAGES[self.user['role']]:
            raise AccessDenied('Your role cannot access this workspace.')

    def records(self,query,params=()):
        allowed=TABLES[self.user['role']]
        def authorize(action,arg1,arg2,database,trigger):
            if action==sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg1 in allowed else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY
        with connection(self.path) as conn:
            conn.set_authorizer(authorize)
            try:
                return [dict(r) for r in conn.execute(query,params)]
            except sqlite3.DatabaseError as exc:
                raise AccessDenied('This data request is not permitted for your role.') from exc

    def __getattr__(self,name):
        if name not in ALL_METHODS:
            raise AttributeError(name)
        method=getattr(self._core,name)
        def invoke(*args,**kwargs):
            user=self.user
            allowed=user['role']=='admin' or name in METHODS.get(user['role'],set())
            bound=inspect.signature(method).bind(*args,**kwargs)
            bound.apply_defaults()
            if allowed and name in ('transition','admit') and user['role']!='admin':
                values=bound.arguments
                visits=self._core.records('SELECT state,ward_id FROM visits WHERE visit_id=?',(values['visit_id'],))
                state=visits[0]['state'] if visits else None
                if user['role']=='pharmacy':
                    allowed=state=='PHARMACY' and values.get('target')=='COMPLETED'
                elif user['role']=='ward':
                    allowed=state=='ADMITTED' and (name=='admit' and visits[0]['ward_id'] is None or values.get('target') in ('COMPLETED','TRANSFER_REQUIRED'))
                elif user['role']=='physician':
                    allowed=state in ('CONSULTATION','DIAGNOSTICS','ADMITTED')
            if not allowed:
                with connection(self.path,write=True) as conn:
                    conn.execute('INSERT INTO staff_audit(user_id,action,outcome) VALUES (?,?,?)',(user['user_id'],name,'DENIED'))
                raise AccessDenied('Your role cannot perform this action in the current state.')
            marker=actor.set(user['user_id'])
            try:
                return method(*args,**kwargs)
            finally:
                actor.reset(marker)
        return invoke
