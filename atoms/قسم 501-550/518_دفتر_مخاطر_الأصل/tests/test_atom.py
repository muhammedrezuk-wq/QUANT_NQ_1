import asyncio,importlib.util,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(folder));spec=importlib.util.spec_from_file_location('_t518',folder/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules['_t518']=m;spec.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def test_broker_scoped_ledger_and_reservations():
 # v4.2.0: was an un-prefixed main() -- invisible to `pytest`/the official
 # governance/scripts/test_atoms.py runner, collectible only by running
 # this file directly as a script. Renamed to a real test_* function so
 # it actually runs under CI; logic and assertions are untouched.
 b=B();a=m.Atom();td=tempfile.TemporaryDirectory();await a.initialize(m.AtomContext(518,{'default_risk_budget':0,'count_realized':True,'max_seen_trades':100,'consumer_db_path':td.name+'/c.db'},L(),b.publish,b.subscribe));await a.start();await a._on_account({'account_id':'A','broker':'BR'});await a._on_specs({'symbols':[{'account_id':'A','symbol':'NQ','tick_size':.25,'tick_value':5}]});await a._on_budget({'account_id':'A','broker':'BR','symbol':'NQ','risk_budget':50});await a._on_positions({'account_id':'A','broker':'BR','source':'609','timestamp':2,'complete':True,'positions':[{'account_id':'A','broker':'BR','symbol':'NQ','ticket':1,'side':'BUY','volume':1,'entry_price':100,'current_price':101}]});row=[p for n,p in b.e if n==m.EVENT_OUT][-1]['ledgers'][0];assert row['floating_economic']==20 and row['broker']=='BR';await a._on_trade({'event_id':'t','account_id':'A','broker':'BR','symbol':'NQ','pnl':10,'gross_pnl':12,'completeness':'COMPLETE'});assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['ledgers'][0]['K']==10;await a._on_order({'account_id':'A','broker':'BR','symbol':'NQ','request_id':'r','action':'OPEN','risk_budget':20});row=[p for n,p in b.e if n==m.EVENT_OUT][-1]['ledgers'][0];assert row['reserved_risk']==20 and row['remaining_risk']<=30;snap=await a.snapshot();c=m.Atom();await c.restore(snap);assert c._reservations;print('518 broker-scoped ledger and reservation tests passed')
async def test_spec_fallback_across_accounts_same_broker():
    """استعادة سلوك احتياطي انقطع بصمت أثناء إعادة هيكلة متعددة الوسطاء (مقيس 2026-08-19
    بمقارنة لقطة 2026-08-15): مواصفة رمز معروفة لحساب آخر بنفس الوسيط ونفس الرمز تُستخدم
    كتقريب آمن ريثما تصل مواصفة هذا الحساب بالذات -- محافظ عمداً: لا يتخطى حدود الوسيط
    نفسه أبداً (لا يُخلط بين وسيطين مختلفين)."""
    b = B(); a = m.Atom(); td = tempfile.TemporaryDirectory()
    await a.initialize(m.AtomContext(518, {'default_risk_budget': 0, 'count_realized': True,
                                            'max_seen_trades': 100, 'consumer_db_path': td.name + '/c2.db'},
                                      L(), b.publish, b.subscribe))
    await a.start()
    await a._on_account({'account_id': 'A', 'broker': 'BR'})
    await a._on_specs({'symbols': [{'account_id': 'A', 'symbol': 'NQ', 'tick_size': .25, 'tick_value': 5}]})
    await a._on_account({'account_id': 'C', 'broker': 'BR'})
    await a._on_budget({'account_id': 'C', 'broker': 'BR', 'symbol': 'NQ', 'risk_budget': 50})
    await a._on_positions({'account_id': 'C', 'broker': 'BR', 'source': '609', 'timestamp': 2, 'complete': True,
                           'positions': [{'account_id': 'C', 'broker': 'BR', 'symbol': 'NQ', 'ticket': 9,
                                         'side': 'BUY', 'volume': 1, 'entry_price': 100, 'current_price': 101}]})
    ledgers = [p for n, p in b.e if n == m.EVENT_OUT][-1]['ledgers']
    c_row = [r for r in ledgers if r['account_id'] == 'C'][0]
    assert c_row['floating_economic'] == 20, (
        "يجب أن يستعمل مواصفة حساب A كتقريب (tick_size=0.25, tick_value=5) بدل الرجوع "
        "لربح الوسيط الخام أو صفر -- floating_economic الفعلي: %r" % (c_row['floating_economic'],))
    assert 'MISSING_SYMBOL_SPECS' not in c_row['warnings'], c_row['warnings']
    print('OK — 518 spec fallback across accounts, same broker only')


if __name__=='__main__':asyncio.run(test_broker_scoped_ledger_and_reservations());asyncio.run(test_spec_fallback_across_accounts_same_broker())
