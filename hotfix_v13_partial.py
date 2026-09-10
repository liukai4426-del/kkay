from pathlib import Path


def once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(f'{label}: expected 1 match, got {text.count(old)}')
    return text.replace(old,new,1)

p=Path('engine.py'); s=p.read_text()
old="""    def stop(self):
        self.enabled=False; self.stopped=True
        p=self.store.data.get('active') if self.store else None
        if p and not p.get('filled') and not p.get('cancel_requested') and not self.x.positions():
            self._cancel_pending_entry(p,'手动停止自动交易')
        self.emit('log','已停止新开仓；未成交限价开仓会撤销，已有仓位继续监控且交易所TP/SL不撤销')
"""
new="""    def stop(self):
        self.enabled=False; self.stopped=True
        p=self.store.data.get('active') if self.store else None
        # Do not issue a cancel blindly here: the limit may have filled between UI clicks and REST reads.
        # Reconcile order + position state first, then cancel only a genuinely unfilled/partial parent order.
        if p and not p.get('filled') and not p.get('cancel_requested'):
            p['cancel_on_reconcile']=True; self.store.save()
        self.emit('log','已停止新开仓；未成交限价开仓会在状态核对后撤销，已有仓位继续监控且交易所TP/SL不撤销')
"""
s=once(s,old,new,'stop reconcile-first')
s=once(s,
"state['active']=dict(plan,client_id=cid,algo_id=aid,submitted=time.time(),expires=time.time()+300,equity_before=equity,filled=False,cancel_requested=False,\n                             score=score['total'],score_items=score.get('items',[]))",
"state['active']=dict(plan,client_id=cid,algo_id=aid,submitted=time.time(),expires=time.time()+300,equity_before=equity,filled=False,cancel_requested=False,cancel_on_reconcile=False,partial_close_id='',\n                             score=score['total'],score_items=score.get('items',[]))",
'active lifecycle metadata')
old="""        if status in ('live','partially_filled'):
            if filled>0 or positions:
                self._cancel_pending_entry(p,'限价单出现部分成交，撤销剩余数量并核对保护')
                return
            if p.get('cancel_requested'):
                if time.time()-float(p['cancel_requested'])>15:
                    raise Halt('限价撤单状态长时间无法核实；请到OKX核对，禁止重复开仓')
                return
            if time.time() >= float(p.get('expires',p['submitted']+300)):
                self._cancel_pending_entry(p,'限价挂单已等待1根5m K线')
            return
        if status=='canceled' and filled<=0:
"""
new="""        if status in ('live','partially_filled'):
            if p.get('cancel_requested'):
                if time.time()-float(p['cancel_requested'])>15:
                    raise Halt('限价撤单状态长时间无法核实；请到OKX核对，禁止重复开仓')
                return
            if filled>0 or positions:
                self._cancel_pending_entry(p,'限价单出现部分成交，先撤销剩余数量')
                return
            if p.get('cancel_on_reconcile'):
                self._cancel_pending_entry(p,'手动停止自动交易')
                return
            if time.time() >= float(p.get('expires',p['submitted']+300)):
                self._cancel_pending_entry(p,'限价挂单已等待1根5m K线')
            return
        if status=='canceled' and filled<=0:
"""
s=once(s,old,new,'pending/cancel lifecycle')
old="""        if status not in ('filled','canceled'):
            if time.time()-p['submitted']>15:
                raise Halt('订单状态长时间不确定，禁止重复开仓；请到OKX核对')
            return
        if filled<=0:
            raise Halt('成交状态异常')
        p['filled']=True; p['filled_sz']=filled; self.store.save()
        if not positions:
            if time.time()-p['submitted']<10:
                return
"""
new="""        # OKX attached TP/SL is created only after the parent order is fully filled.
        # A canceled parent with partial fills is therefore flattened at market instead of left unprotected.
        if status=='canceled' and filled>0 and positions:
            if p.get('partial_close_id'):
                if time.time()-float(p.get('partial_close_requested',time.time()))>15:
                    raise Halt('部分成交仓位的市价安全平仓结果长时间无法核实；请立即到OKX核对')
                return
            size=sum(abs(float(r['pos'])) for r in positions)
            if size<=0 or size>float(p['sz'])+1e-10:
                raise Halt('部分成交仓位数量异常，请立即到OKX核对')
            close_id='pc'+uuid.uuid4().hex[:28]
            p['partial_close_id']=close_id; p['partial_close_requested']=time.time(); self.store.save()
            self.x.post('/api/v5/trade/order',{'instId':INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],
                'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':str(size),'clOrdId':close_id})
            self.store.record('部分成交安全平仓请求',{'client_id':p['client_id'],'close_id':close_id,'size':size})
            self.emit('log','限价开仓仅部分成交：剩余挂单已撤销，已对成交部分发送市价安全平仓请求；禁止重复发送')
            return
        if status not in ('filled','canceled'):
            if time.time()-p['submitted']>15:
                raise Halt('订单状态长时间不确定，禁止重复开仓；请到OKX核对')
            return
        if filled<=0:
            raise Halt('成交状态异常')
        if not p.get('filled'):
            p['filled']=True; p['filled_sz']=filled; p['filled_at']=time.time(); self.store.save()
        if not positions:
            if time.time()-float(p.get('filled_at',p['submitted']))<10:
                return
"""
s=once(s,old,new,'partial flatten and fill timestamp')
s=s.replace("elif time.time()-p['submitted']>15:\n            self.halt('止盈止损保护状态无法核实", "elif time.time()-float(p.get('filled_at',p['submitted']))>15:\n            self.halt('止盈止损保护状态无法核实",1)
p.write_text(s)

# Add regression tests for stop race, partial fill flattening, and delayed full-fill protection grace.
p=Path('test_v13.py'); s=p.read_text()
marker="\n\nif __name__=='__main__': unittest.main(verbosity=2)\n"
extra=r'''

class V13LimitFillSafetyTests(unittest.TestCase):
    def make_engine(self,folder):
        x=test_engine.FakeExchange(); e=Engine(x,folder); e.connect(); e.arm(Settings())
        e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                  'h':{'atr':2000},'m':{'atr':200},
                  'scores':{'做多':{'gate':True,'total':8,'level':'普通信号','items':[]}}}
        e.market_at=time.time(); e.market_monotonic=time.monotonic(); e.cycle()
        return x,e,e.store.data['active']

    def test_stop_marks_cancel_then_reconcile_cancels_unfilled(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder); before=len(x.writes)
            e.stop()
            self.assertEqual(len(x.writes),before)
            self.assertTrue(e.store.data['active']['cancel_on_reconcile'])
            x.ord={'state':'live','accFillSz':'0'}; e.cycle()
            self.assertTrue(any(path.endswith('/cancel-order') for path,_ in x.writes[before:]))

    def test_partial_fill_cancel_then_market_flatten(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder)
            qty=float(p['sz'])/2
            x.pos=[{'mgnMode':'isolated','posSide':p['posSide'],'pos':str(qty)}]
            x.ord={'state':'partially_filled','accFillSz':str(qty)}
            e.cycle()
            self.assertEqual(x.writes[-1][0],'/api/v5/trade/cancel-order')
            x.ord={'state':'canceled','accFillSz':str(qty)}
            e.cycle()
            path,body=x.writes[-1]
            self.assertEqual(path,'/api/v5/trade/order'); self.assertEqual(body['ordType'],'market')
            self.assertEqual(body['posSide'],p['posSide']); self.assertTrue(e.store.data['active']['partial_close_id'])

    def test_late_full_fill_gets_fresh_protection_grace(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder)
            p['submitted']=time.time()-240; e.store.save()
            x.ord={'state':'filled','accFillSz':p['sz']}
            x.pos=[{'mgnMode':'isolated','posSide':p['posSide'],'pos':p['sz']}]
            x.protections=[]
            e.cycle()
            self.assertTrue(e.store.data['active']['filled'])
            self.assertGreater(e.store.data['active']['filled_at'],time.time()-5)
            self.assertEqual(e.store.data.get('halt',''),'')
'''
if marker not in s:
    raise RuntimeError('test insertion marker missing')
s=s.replace(marker,extra+marker,1)
p.write_text(s)

p=Path('RELEASE-1.3.md'); s=p.read_text()
needle='- 开仓改为普通限价委托，最多等待1根5m K线；未成交自动撤销，不算一笔交易。\n'
addition='- 限价若部分成交：先撤销剩余挂单，确认父单取消后立即市价平掉已成交部分；不把部分成交裸仓交给尚未生成的附带TP/SL。\n'
if addition not in s:
    if needle not in s: raise RuntimeError('release insertion point missing')
    s=s.replace(needle,needle+addition,1)
p.write_text(s)

print('V1.3 partial-fill safety hotfix applied')
