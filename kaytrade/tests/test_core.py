import copy,tempfile,unittest,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Engine,Error,validate,digest
P={'id':'test-v1','environment':'demo','confirm_before':'2099-01-01T00:00:00+08:00','direction':'short','leverage':20,'validity_seconds':3600,'strategy':'countertrend_structure_mean_reversion','fixed_invalidation':'85900','legs':[{'entry':'85000','size':'0.5','tp_trigger':'84000','tp_limit':'84010','sl_trigger':'85500','sl_limit':'85520','funding_buffer':'0.3'},{'entry':'85150','size':'0.5','tp_trigger':'84150','tp_limit':'84160','sl_trigger':'85650','sl_limit':'85670','funding_buffer':'0.3'}]}
class Fake:
 environment='demo'
 def __init__(self):self.posts=[];self.orders={};self.fail=False;self.occupied=False;self.maker='-0.0002'
 def get(self,path,**q):
  if path.endswith('/config'):return [{'uid':'fake-account','posMode':'net_mode'}]
  if path.endswith('/positions'):return [{'pos':'1'}] if self.occupied else []
  if path.endswith('orders-pending') or path.endswith('orders-algo-pending'):return []
  if path.endswith('/instruments'):return [{'state':'live','ctValCcy':'BTC','ctVal':'0.01','ctMult':'1','lotSz':'0.01','minSz':'0.01','tickSz':'0.1'}]
  if path.endswith('/trade-fee'):return [{'makerU':self.maker,'takerU':'-0.0005'}]
  if path.endswith('/ticker'):return [{'bidPx':'84900','askPx':'84900.1'}]
  if path.endswith('/balance'):return [{'details':[{'ccy':'USDT','availBal':'1000'}]}]
  if path.endswith('/order'):return [self.orders[q['clOrdId']]]
  raise AssertionError(path)
 def post(self,path,body):
  self.posts.append((path,body))
  if path.endswith('/order'):
   self.orders[body['clOrdId']]={'state':'live','cTime':str(int(time.time()*1000)),'accFillSz':'0','ordId':str(len(self.orders)+1)}
   if self.fail:raise Error('Timeout')
  if path.endswith('/cancel-order'):self.orders[body['clOrdId']]['state']='canceled'
  return []
class Tests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.api=Fake();self.e=Engine(self.api,Path(self.tmp.name)/'db')
 def tearDown(self):self.e.db.close();self.tmp.cleanup()
 def test_duplicate_confirmation(self):
  v=self.e.import_plan(copy.deepcopy(P));self.e.confirm(P['id'],v['approval'])
  with self.assertRaises(Error):self.e.confirm(P['id'],v['approval'])
  self.assertEqual(sum(x.endswith('/order') for x,b in self.api.posts),2)
 def test_wrong_confirmation(self):
  self.e.import_plan(copy.deepcopy(P))
  with self.assertRaises(Error):self.e.confirm(P['id'],'wrong')
  self.assertFalse(self.api.posts)
 def test_environment(self):
  with self.assertRaises(Error):validate(P,'live')
 def test_expired(self):
  p=copy.deepcopy(P);p['confirm_before']='2020-01-01T00:00:00Z'
  with self.assertRaises(Error):validate(p,'demo')
 def test_occupied_account(self):
  self.api.occupied=True
  with self.assertRaises(Error):self.e.import_plan(copy.deepcopy(P))
  self.assertFalse(self.api.posts)
 def test_step(self):
  p=copy.deepcopy(P);p['legs'][0]['size']='0.501'
  with self.assertRaises(Error):self.e.import_plan(p)
 def test_low_rr(self):
  p=copy.deepcopy(P);p['legs'][0].update(sl_trigger='85100',sl_limit='85120',tp_trigger='84800',tp_limit='84810')
  with self.assertRaises(Error):self.e.import_plan(p)
 def test_fee_changed(self):
  v=self.e.import_plan(copy.deepcopy(P));self.api.maker='-0.0003'
  with self.assertRaises(Error):self.e.confirm(P['id'],v['approval'])
  self.assertFalse(self.api.posts)
 def test_timeout_no_replay(self):
  v=self.e.import_plan(copy.deepcopy(P));self.api.fail=True
  with self.assertRaises(Error):self.e.confirm(P['id'],v['approval'])
  self.e.reconcile();self.assertEqual(sum(x.endswith('/order') for x,b in self.api.posts),1)
 def test_partial_expiry(self):
  v=self.e.import_plan(copy.deepcopy(P));self.e.confirm(P['id'],v['approval'])
  for o in self.api.orders.values():o.update(cTime=str(int((time.time()-4000)*1000)),state='partially_filled',accFillSz='0.1')
  s=self.e.reconcile();self.assertTrue(all(o['status']=='canceled' for o in s['orders']));self.assertTrue(any(x['action']=='protection_attention' for x in s['events']))
 def test_bad_field(self):
  p=copy.deepcopy(P);p['ignore_risk']=True
  with self.assertRaises(Error):validate(p,'demo')
 def test_nonfinite(self):
  p=copy.deepcopy(P);p['legs'][0]['entry']='NaN'
  with self.assertRaises(Error):validate(p,'demo')
if __name__=='__main__':unittest.main()
