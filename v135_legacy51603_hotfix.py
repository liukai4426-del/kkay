from pathlib import Path


def replace_once(path, old, new):
    p=Path(path)
    text=p.read_text()
    count=text.count(old)
    if count!=1:
        raise SystemExit(f'{path}: expected 1 anchor, found {count}: {old[:100]!r}')
    p.write_text(text.replace(old,new,1))


engine_anchor="""    def connect(self):
        self.x.sync_time()
        a=self.x.account()
        uid=a.get('uid')
        if not uid:
            raise Halt('账户标识缺失')
        name=hashlib.sha256((self.x.host+str(self.x.demo)+uid).encode()).hexdigest()[:24]
        self.store=Store(self.folder/(name+'.json'))
        self.connection_id=uid
        self.emit('account',{'environment':'OKX模拟盘' if self.x.demo else '真实账户','mode':a.get('posMode'),
                             'equity':self.x.balance()[0],'positions':self.x.positions(),'orders':self.x.orders()})
        self.emit('log','只读连接检查成功；未开仓')
"""
engine_new="""    @staticmethod
    def _is_legacy_51603_lock(reason):
        text=normalize_halt_reason(reason)
        return 'OKX错误码 51603' in text and '核对环境、权限、地区与系统时间' in text

    def _migrate_legacy_51603_lock(self):
        \"\"\"Safely release only the known V1.3.4 false-positive 51603 lock when exposure is provably empty.\"\"\"
        if not self.store or not self._is_legacy_51603_lock(self.store.data.get('halt','')):
            return False
        positions=self.x.positions(); orders=self.x.orders(); algos=self.x.algos()
        if positions or orders or algos:
            self.store.data['halt']='历史51603故障锁：检测到BTC仓位或挂单，需人工核对后解除'
            self.store.save()
            self.emit('log','检测到旧版51603故障锁，但账户仍有BTC仓位/挂单；保留故障锁，不自动恢复')
            return False
        p=self.store.data.get('active')
        if p:
            try:
                order=self.x.order(p.get('client_id',''),p.get('order_id',''))
            except Exception as exc:
                if str(getattr(exc,'code',''))!='51603':
                    raise
                order=None
            if order is not None:
                state=str(order.get('state') or '')
                filled=float(order.get('accFillSz') or 0)
                if state not in ('canceled','filled') or filled>0:
                    self.store.data['halt']='历史51603故障锁：原订单存在成交或状态仍需核对，必须人工解除'
                    self.store.save()
                    self.emit('log','旧版51603锁关联订单仍有成交/非终态证据；保留故障锁，需人工核对')
                    return False
            history=self.x.recent_orders() if hasattr(self.x,'recent_orders') else []
            def same(row):
                return bool(isinstance(row,dict) and (
                    (p.get('order_id') and str(row.get('ordId') or '')==str(p.get('order_id'))) or
                    (p.get('client_id') and str(row.get('clOrdId') or '')==str(p.get('client_id')))
                ))
            found=next((row for row in history if same(row)),None)
            if found:
                state=str(found.get('state') or '')
                filled=float(found.get('accFillSz') or 0)
                if state not in ('canceled','filled') or filled>0:
                    self.store.data['halt']='历史51603故障锁：历史订单存在成交或状态仍需核对，必须人工解除'
                    self.store.save()
                    self.emit('log','旧版51603锁在历史订单中存在成交/非终态证据；保留故障锁，需人工核对')
                    return False
        old_client=p.get('client_id') if isinstance(p,dict) else ''
        old_order=p.get('order_id') if isinstance(p,dict) else ''
        self.store.data['active']=None
        self.store.data['halt']=''
        self.store.save()
        self.store.record('V1.3.5旧51603锁安全迁移',{'client_id':old_client,'order_id':old_order})
        self.emit('log','V1.3.5已安全迁移旧51603故障锁：确认BTC持仓、普通委托、策略委托及相关未成交订单均为空；旧锁已解除')
        return True

    def connect(self):
        self.x.sync_time()
        a=self.x.account()
        uid=a.get('uid')
        if not uid:
            raise Halt('账户标识缺失')
        name=hashlib.sha256((self.x.host+str(self.x.demo)+uid).encode()).hexdigest()[:24]
        self.store=Store(self.folder/(name+'.json'))
        self.connection_id=uid
        self._migrate_legacy_51603_lock()
        self.emit('account',{'environment':'OKX模拟盘' if self.x.demo else '真实账户','mode':a.get('posMode'),
                             'equity':self.x.balance()[0],'positions':self.x.positions(),'orders':self.x.orders()})
        self.emit('log','只读连接检查成功；未开仓')
"""
replace_once('engine.py',engine_anchor,engine_new)

# Add regression tests before adapter tests.
test_anchor="\n\nclass V135AdapterTests(unittest.TestCase):\n"
test_block="""

class V135Legacy51603MigrationTests(unittest.TestCase):
    OLD='OKX错误码 51603（核对环境、权限、地区与系统时间）'

    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange(); self.events=[]
        self.x.recent_orders=lambda: []
        first=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        first.connect()
        self.path=first.store.path

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self,active=None,halt=None):
        from engine import Store
        store=Store(self.path)
        store.data['active']=active
        store.data['halt']=self.OLD if halt is None else halt
        store.save()

    def reconnect(self):
        e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        e.connect()
        return e

    def test_old_51603_flat_missing_order_is_auto_migrated(self):
        self.seed({'client_id':'legacy-client','order_id':''})
        self.x.order=lambda cid='',order_id='': (_ for _ in ()).throw(APIError('OKX错误码 51603','51603'))
        e=self.reconnect()
        self.assertEqual(e.store.data['halt'],'')
        self.assertIsNone(e.store.data['active'])
        self.assertTrue(any(k=='log' and '旧51603故障锁' in str(v) and '已解除' in str(v) for k,v in self.events))

    def test_old_51603_with_live_exposure_stays_locked(self):
        self.seed(None)
        self.x.pos=[{'mgnMode':'isolated','posSide':'long','pos':'0.01'}]
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])
        self.assertIn('历史51603故障锁',e.store.data['halt'])

    def test_old_51603_with_historical_fill_stays_locked(self):
        self.seed({'client_id':'legacy-client','order_id':'123'})
        self.x.order=lambda cid='',order_id='': (_ for _ in ()).throw(APIError('OKX错误码 51603','51603'))
        self.x.recent_orders=lambda: [{'ordId':'123','clOrdId':'legacy-client','state':'filled','accFillSz':'0.01'}]
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])
        self.assertIsNotNone(e.store.data['active'])

    def test_new_51603_lock_is_never_auto_cleared(self):
        self.seed(None,'51603：OKX暂未找到该订单，正在核对订单与持仓状态')
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])


class V135AdapterTests(unittest.TestCase):
"""
replace_once('test_v135.py',test_anchor,test_block)

print('V1.3.5 legacy 51603 migration hotfix applied')
