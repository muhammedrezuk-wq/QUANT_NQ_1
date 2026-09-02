import asyncio,importlib.util,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(root/'clock'));sys.path.insert(0,str(folder));import clock
spec=importlib.util.spec_from_file_location('_t552',folder/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules['_t552']=m;spec.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
def rejected(b):return [p for n,p in b.e if n==m.EVENT_REJECTED]
def finals(b):return [p for n,p in b.e if n==m.EVENT_FINAL]
async def fresh():
 """بوابة جاهزة بكل تراخيصها: ساعة، حساب، تفعيل NQ، مطابقة، مرجع، تعرض."""
 b=B();a=m.Atom();await a.initialize(m.AtomContext(552,{'enabled':True,'max_spread_points':0},L(),b.publish,b.subscribe));await a.start()
 await a._on_account({'account_id':'A','broker':'BR'})
 await a._on_whitelist({'allowed_by_account':{'A':['NQ']}})
 await a._on_reconcile({'account_id':'A','broker':'BR','symbol':'*','status':'MATCH_EMPTY_ACCOUNT'})
 await a._on_reference({'symbol':'NQ','state':'HEALTHY'})
 await a._on_exposure({'account_id':'A','broker':'BR','usable_for_new_exposure':True})
 return a,b
def order(**over):
 o={'magic':20260801,'account_id':'A','broker':'BR','request_id':'r','action':'OPEN','symbol':'NQ','side':'BUY','volume':1,'reference_price':100,'stop_loss':99,'take_profit':102}
 o.update(over);return o
async def main():
 clock.reset_for_tests();clock.configure(max_accepted_offset_s=5,max_sample_age_s=30,stale_after_s=900,max_slew_per_second=.05);clock.accept_sample({'median_offset_s':.1,'measured_at':time.time(),'quorum':True},writer='003')

 # --- السيناريو الأصلي محدّثًا لعقد بند 22 حزمة ت: أمر اتجاهي كامل الهوية ---
 b=B();a=m.Atom();await a.initialize(m.AtomContext(552,{'enabled':True,'max_spread_points':0},L(),b.publish,b.subscribe));await a.start()
 await a._on_account({'account_id':'A','broker':'BR'});await a._on_whitelist({'allowed_by_account':{'A':['NQ']}})
 await a._on_margin_verdict({'account_id':'A','request_id':'r','approved':True,'required_margin':50.0,'free_margin':1000.0})
 o=order(decision_id='D1',gate_request_id='G1')
 await a._on_built(o)
 last=rejected(b)[-1];assert last['reason']=='RECONCILIATION_NOT_MATCHED' and last['stage']=='FINAL_VALIDATION',last
 await a._on_reconcile({'account_id':'A','broker':'BR','symbol':'*','status':'MATCH_EMPTY_ACCOUNT'});await a._on_reference({'symbol':'NQ','state':'HEALTHY'});await a._on_exposure({'account_id':'A','broker':'BR','usable_for_new_exposure':True})
 await a._on_built(o)
 done=finals(b);assert done,'الأمر كامل الهوية والتراخيص لازم يمر'
 # ت١: الحقلان يمران بالقرار النهائي كما وصلا
 assert done[-1]['decision_id']=='D1' and done[-1]['gate_request_id']=='G1',done[-1]
 assert 'identity_warnings' not in done[-1],'هوية كاملة بلا إنذار'
 await a._on_halt({'account_id':'A'});await a._on_built(o)
 last=rejected(b)[-1];assert last['reason']=='halted' and last['stage']=='FINAL_VALIDATION';assert 'A' in a._halted_accounts
 print('552: السيناريو الأصلي محدّثًا — هوية تمر، مطابقة، إيقاف حساب')

 # --- (أ) طلب اتجاهي بلا قرار أب ولا أصل مالك ⇒ رفض بمرحلته ---
 a,b=await fresh()
 await a._on_margin_verdict({'account_id':'A','request_id':'r','approved':True})
 await a._on_built(order())
 last=rejected(b)[-1]
 assert last['reason']=='PARENT_DECISION_MISSING' and last['stage']=='PARENT_DECISION',last
 assert last['barrier']['name']=='PARENT_DECISION' and last['barrier']['measured_at'] is not None,last['barrier']
 assert a._parent_decision_blocked==1
 print('552 (أ): اتجاهي بلا قرار أب ولا مالك ⇒ رفض PARENT_DECISION')

 # --- (ب) الأصل غير مفعّل ⇒ رفض بمرحلة التفعيل ---
 a,b=await fresh()
 await a._on_built(order(decision_id='D1',symbol='XX'))
 last=rejected(b)[-1]
 assert last['reason']=='SYMBOL_NOT_ALLOWED' and last['stage']=='ASSET_ACTIVATION',last
 assert last['barrier']['value']=='XX' and last['barrier']['threshold']==['NQ'],last['barrier']
 print('552 (ب): أصل غير مفعّل ⇒ رفض ASSET_ACTIVATION برباعية الحاجز')

 # --- (ج) حكم الهامش: غائب ثم رافض ---
 a,b=await fresh()
 await a._on_built(order(decision_id='D1',request_id='r2'))
 last=rejected(b)[-1]
 assert last['reason']=='MARGIN_VERDICT_MISSING' and last['stage']=='MARGIN_VERDICT',last
 await a._on_margin_verdict({'account_id':'A','request_id':'r3','approved':False,'reason':'INSUFFICIENT_FREE_MARGIN','required_margin':500.0,'free_margin':100.0})
 await a._on_built(order(decision_id='D1',request_id='r3'))
 last=rejected(b)[-1]
 assert last['reason']=='MARGIN_VERDICT_REJECTED' and last['stage']=='MARGIN_VERDICT',last
 assert last['barrier']['value']==500.0 and last['barrier']['threshold']==100.0 and last['barrier']['measured_at'] is not None,last['barrier']
 assert a._margin_verdict_blocked==2
 print('552 (ج): حكم الهامش الغائب والرافض ⇒ رفض MARGIN_VERDICT بقيمه')

 # --- (د) صلاحية اللقطة: مجهولة ثم غير صالحة ---
 a,b=await fresh()
 await a._on_margin_verdict({'account_id':'A','request_id':'r','approved':True})
 await a._on_built(order(decision_id='D1',snapshot_id='ghost'))
 last=rejected(b)[-1]
 assert last['reason']=='SNAPSHOT_UNKNOWN' and last['stage']=='SNAPSHOT_VALIDITY',last
 await a._on_snapshot({'snapshot_id':'snap-2','snapshot_status':'INCOMPLETE','usable_for_new_exposure':False,'usable_for_protection':False})
 await a._on_built(order(decision_id='D1',snapshot_id='snap-2'))
 last=rejected(b)[-1]
 assert last['reason']=='SNAPSHOT_NOT_USABLE' and last['stage']=='SNAPSHOT_VALIDITY',last
 assert last['barrier']['value']=='INCOMPLETE' and last['barrier']['threshold']=='READY',last['barrier']
 assert a._snapshot_validity_blocked==2
 print('552 (د): لقطة مجهولة أو غير صالحة ⇒ رفض SNAPSHOT_VALIDITY')

 # --- أمر مالك موثق (owner_command_id) يمر رغم غياب decision_id ---
 a,b=await fresh()
 await a._on_margin_verdict({'account_id':'A','request_id':'r','approved':True})
 await a._on_built(order(owner_command_id='OC-1'))
 done=finals(b);assert done,'أمر المالك الموثق لازم يمر'
 assert done[-1]['owner_command_id']=='OC-1'
 assert done[-1]['decision_id'] is None and done[-1]['identity_missing']==['decision_id','gate_request_id'],done[-1]
 assert done[-1]['identity_warnings']==['identity_incomplete'],'الغائب يمر None مع الإنذار — لا اختراع'
 print('552: أمر مالك موثق يمر، والهوية الغائبة None + إنذار')

 # --- ت١: استرجاع الهوية من اللقطة نفسها التي بُني منها الأمر (551 أسقطها) ---
 a,b=await fresh()
 await a._on_margin_verdict({'account_id':'A','request_id':'r','approved':True})
 await a._on_snapshot({'snapshot_id':'snap-1','decision_id':'D9','gate_request_id':'G9','snapshot_status':'READY','usable_for_new_exposure':True,'usable_for_protection':True})
 await a._on_built(order(snapshot_id='snap-1'))
 done=finals(b);assert done,'الاسترجاع من اللقطة يكمل الهوية ويمرر الأمر'
 assert done[-1]['decision_id']=='D9' and done[-1]['gate_request_id']=='G9' and done[-1]['identity_from_snapshot'] is True,done[-1]
 assert a._identity_recovered==1
 print('552: هوية القرار تُسترجَع من سجل اللقطة الموثوق — الخيط لا ينقطع عند 551')

 # --- أوامر الإدارة/الإغلاق معفاة من فحوص الدخول الاتجاهي ---
 a,b=await fresh()
 await a._on_built(order(action='CLOSE_PARTIAL',ticket=7,stop_loss=None,take_profit=None))
 done=finals(b);assert done,'أمر الإدارة يمر بلا قرار أب ولا حكم هامش ولا لقطة'
 assert done[-1]['decision_id'] is None and done[-1]['identity_warnings']==['identity_incomplete']
 print('552: الإدارة/الإغلاق لا يكسرها فحص القرار الأب — كما يميز الكود القائم')

 # --- T4: كاش استرجاع الهوية (اللقطات + أحكام الهامش) ينجو من إعادة التشغيل ---
 a,b=await fresh()
 await a._on_margin_verdict({'account_id':'A','request_id':'r7','approved':True,'required_margin':10.0,'free_margin':500.0})
 await a._on_snapshot({'snapshot_id':'snap-9','decision_id':'D7','gate_request_id':'G7','snapshot_status':'READY','usable_for_new_exposure':True,'usable_for_protection':True})
 state=await a.snapshot()
 assert any(row['snapshot_id']=='snap-9' and row['decision_id']=='D7' for row in state['snapshots']),state['snapshots']
 assert any(row['account_id']=='A' and row['request_id']=='r7' and row['approved'] is True for row in state['margin_verdicts']),state['margin_verdicts']
 a2=m.Atom();await a2.initialize(m.AtomContext(552,{'enabled':True,'max_spread_points':0},L(),b.publish,b.subscribe));await a2.restore(state)
 assert a2._snapshots['snap-9']['decision_id']=='D7' and a2._snapshots['snap-9']['gate_request_id']=='G7',a2._snapshots
 assert a2._margin_verdicts[('A','r7')]['approved'] is True,a2._margin_verdicts
 await a2.start()
 # a2 نفسه بعد "إقلاع" جديد -- يحتاج تراخيصه من جديد (منفصلة عن كاش الاسترجاع)
 await a2._on_account({'account_id':'A','broker':'BR'});await a2._on_whitelist({'allowed_by_account':{'A':['NQ']}})
 await a2._on_reconcile({'account_id':'A','broker':'BR','symbol':'*','status':'MATCH_EMPTY_ACCOUNT'});await a2._on_reference({'symbol':'NQ','state':'HEALTHY'});await a2._on_exposure({'account_id':'A','broker':'BR','usable_for_new_exposure':True})
 await a2._on_margin_verdict({'account_id':'A','request_id':'r8','approved':True})
 await a2._on_built(order(request_id='r8',snapshot_id='snap-9'))
 done=finals(b);assert done and done[-1]['decision_id']=='D7' and done[-1]['identity_from_snapshot'] is True,done[-1]
 print('552 (T4): كاش اللقطات وأحكام الهامش ينجو من restore ويكمل هوية أمر بعد الإقلاع')

 # restore بحالة بلا الحقلين الجديدين (توافق خلفي) يبقى يقفل بأمان
 a3=m.Atom();await a3.initialize(m.AtomContext(552,{'enabled':True,'max_spread_points':0},L(),b.publish,b.subscribe))
 await a3.restore({'global_halted':False,'halted_accounts':{}})
 assert a3._snapshots=={} and a3._margin_verdicts=={},'حالة قديمة بلا الكاشين -- تُقرأ فارغة لا خطأ'
 print('552 (T4): استرجاع حالة قديمة بلا الكاشين الجديدين لا يكسر شيئًا')

 # --- بند 25: حرّاس الحالة الخمسة تتجاهل self._running (مقفلة v5.4.1،
 # مقيسة حيّاً مرّتين: قائمة بيضاء بلعت أول قائمتها، ثمّ RECONCILIATION_
 # NOT_MATCHED). منذ v5.5.0 هذه الدوال مفوَّضة فعليًا لـstate_inputs.py
 # (بعد إصلاحه وإثباته)، فهذا الاختبار يقفل السلوك الصحيح على المسار
 # الحيّ نفسه لا على نسخة افتراضية -- سقوطه يعني عودة العلّتين المقيستين.
 a4=m.Atom();await a4.initialize(m.AtomContext(552,{'enabled':True,'max_spread_points':0},L(),b.publish,b.subscribe))
 assert a4._running is False,'لم يُستدعَ start() بعد -- هذا هو المطلوب لهذا الاختبار'
 await a4._on_margin_verdict({'account_id':'A','request_id':'r9','approved':True,'required_margin':1.0,'free_margin':9.0})
 await a4._on_snapshot({'snapshot_id':'snap-x','decision_id':'D','gate_request_id':'G','snapshot_status':'READY','usable_for_new_exposure':True,'usable_for_protection':True})
 await a4._on_reconcile({'account_id':'A','broker':'BR','symbol':'NQ','status':'match'})
 await a4._on_exposure({'account_id':'A','broker':'BR','usable_for_new_exposure':True})
 await a4._on_reference({'symbol':'NQ','state':'healthy'})
 assert ('A','r9') in a4._margin_verdicts,'حكم الهامش سقط قبل start() -- عودة لعلّة القائمة البيضاء المقيسة حيّاً'
 assert 'snap-x' in a4._snapshots,'اللقطة سقطت قبل start()'
 assert a4._reconcile.get(('A','BR','NQ'))=='MATCH','المطابقة سقطت قبل start()'
 assert ('A','BR') in a4._exposure,'التعرض سقط قبل start()'
 assert a4._reference.get('NQ')=='HEALTHY','المرجع سقط قبل start()'
 print('552 (بند 25): خمسة حرّاس حالة تُخزّن قبل start() -- يقفل v5.4.1 اختباريًّا لا تفسيرًا فقط')
 print('552 all gate tests passed')
if __name__=='__main__':asyncio.run(main())
