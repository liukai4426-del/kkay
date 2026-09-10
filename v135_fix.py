from pathlib import Path


def replace_once(path, old, new):
    p=Path(path); text=p.read_text()
    count=text.count(old)
    if count!=1:
        raise SystemExit(f'{path}: expected 1 anchor, found {count}: {old[:80]!r}')
    p.write_text(text.replace(old,new,1))


def replace_all(path, old, new):
    p=Path(path); text=p.read_text()
    if old not in text:
        raise SystemExit(f'{path}: missing anchor {old!r}')
    p.write_text(text.replace(old,new))

# --- engine.py: remove 5% hard cap and retain daily/notional safety ---
replace_once('engine.py',
"        if self.risk_pct>5 or self.risk_usdt>self.capital*.05:\n            raise Halt('单笔风险不得超过配置资金的5%')\n",
"")
replace_once('engine.py',
"    # Score tiers scale the base risk unit, but never bypass the daily remaining loss budget or the 5% absolute per-trade cap.\n    risk=min(base_risk*float(position_multiplier),daily_remaining,s.capital*.05)\n",
"    # V1.3.5 removes the former 5% capital hard cap. Score tiers still cannot bypass the configured daily-loss budget.\n    risk=min(base_risk*float(position_multiplier),daily_remaining)\n")
replace_all('engine.py','V1.3.4','V1.3.5')

# Persist exchange ordId from successful parent-order acknowledgement.
replace_once('engine.py',
"        self.x.post('/api/v5/trade/order',body)\n        self.store.record('提交开仓请求',state['active'])\n",
"        reply=self.x.post('/api/v5/trade/order',body)\n        row=reply[0] if reply else {}\n        order_id=str(row.get('ordId') or '') if isinstance(row,dict) else ''\n        if not order_id:\n            raise Halt('OKX下单响应缺少ordId；本地已保留请求记录，禁止重复提交，请核对OKX')\n        returned_client=str(row.get('clOrdId') or '') if isinstance(row,dict) else ''\n        if returned_client and returned_client!=cid:\n            raise Halt('OKX返回的clOrdId与本地请求不一致；禁止重复提交，请核对OKX')\n        state['active']['order_id']=order_id\n        state['active']['okx_ack_at']=time.time()\n        self.store.save()\n        self.store.record('提交开仓请求',state['active'])\n")

# Prefer ordId for cancellation once it is known.
replace_once('engine.py',
"        self.x.post('/api/v5/trade/cancel-order',{'instId':INSTRUMENT,'clOrdId':p['client_id']})\n",
"        cancel={'instId':INSTRUMENT}\n        if p.get('order_id'):\n            cancel['ordId']=str(p['order_id'])\n        else:\n            cancel['clOrdId']=p['client_id']\n        self.x.post('/api/v5/trade/cancel-order',cancel)\n")

# Add 51603 reconciliation helper before reconcile().
anchor="    def reconcile(self):\n        state=self.store.data; p=state['active']\n        if not p:\n            return\n        order=self.x.order(p['client_id'])\n"
helper="""    def _same_parent_order(self,row,p):
        if not isinstance(row,dict):
            return False
        return bool((p.get('order_id') and str(row.get('ordId') or '')==str(p['order_id'])) or
                    (p.get('client_id') and str(row.get('clOrdId') or '')==str(p['client_id'])))

    def _lookup_parent_order(self,p):
        try:
            return self.x.order(p.get('client_id',''),p.get('order_id',''))
        except Exception as exc:
            if str(getattr(exc,'code',''))!='51603':
                raise
            now=time.time()
            p['order_missing_checks']=int(p.get('order_missing_checks') or 0)+1
            p['order_missing_last']=now
            self.store.save()
            self.emit('log','51603：OKX暂未找到该订单，正在核对当前委托、历史订单与BTC持仓；不会重复下单')
            pending=self.x.orders()
            found=next((row for row in pending if self._same_parent_order(row,p)),None)
            if found:
                if not p.get('order_id') and found.get('ordId'):
                    p['order_id']=str(found['ordId']); self.store.save()
                return found
            history=self.x.recent_orders() if hasattr(self.x,'recent_orders') else []
            found=next((row for row in history if self._same_parent_order(row,p)),None)
            if found:
                if not p.get('order_id') and found.get('ordId'):
                    p['order_id']=str(found['ordId']); self.store.save()
                return found
            positions=self.x.positions()
            same=[r for r in positions if r.get('mgnMode')=='isolated' and r.get('posSide')==p.get('posSide') and abs(float(r.get('pos') or 0))>0]
            if same:
                if len(same)!=1 or abs(float(same[0].get('pos') or 0))>float(p['sz'])+1e-10:
                    raise Halt('51603：订单查询不到且BTC仓位与本地记录不一致；已停止新开仓，请立即核对OKX')
                # Keep managing the position instead of treating a transient order lookup miss as an absent trade.
                self.emit('log','51603核对发现同方向逐仓BTC持仓：按已成交状态继续检查TP/SL保护，不重复开仓')
                return {'state':'filled','accFillSz':str(abs(float(same[0]['pos']))),'ordId':str(p.get('order_id') or ''),'clOrdId':p.get('client_id','')}
            age=now-float(p.get('submitted',now))
            if age<20:
                return None
            if not p.get('order_id'):
                # Legacy/ambiguous local record: all read-only sources are empty, so it is safe to release the stale parent record.
                self.store.data['active']=None; self.store.save()
                self.store.record('51603确认订单不存在',{'client_id':p.get('client_id'),'checks':p.get('order_missing_checks',0)})
                self.emit('log','51603多路核对确认：无订单、无历史成交、无BTC持仓；已清理本地未成交记录，不计为交易')
                return None
            raise Halt('51603：OKX已返回ordId，但20秒后当前委托、历史订单和BTC持仓仍均无法匹配；已停止新开仓，请核对OKX')

    def reconcile(self):
        state=self.store.data; p=state['active']
        if not p:
            return
        order=self._lookup_parent_order(p)
        if order is None:
            return
"""
replace_once('engine.py',anchor,helper)

# Manual acknowledgement: if account is already empty, 51603 must not prevent clearing a stale lock.
replace_once('engine.py',
"            order=self.x.order(p['client_id'])\n            if order.get('state') not in ('filled','canceled'):\n                raise Halt('原订单仍无法核实，不可解除故障锁')\n            if float(order.get('accFillSz') or 0)>0:\n",
"            try:\n                order=self.x.order(p.get('client_id',''),p.get('order_id',''))\n            except Exception as exc:\n                if str(getattr(exc,'code',''))!='51603':\n                    raise\n                order=None\n            if order is not None and order.get('state') not in ('filled','canceled'):\n                raise Halt('原订单仍无法核实，不可解除故障锁')\n            if order is not None and float(order.get('accFillSz') or 0)>0:\n")

# --- exchange.py: structured OKX error codes + ordId-first lookup + history cross-check ---
replace_once('exchange.py',
"class APIError(RuntimeError):\n    pass\n",
"class APIError(RuntimeError):\n    def __init__(self,message,code=''):\n        super().__init__(message)\n        self.code=str(code or '')\n")
replace_once('exchange.py',
"        if result.get('code')!='0':\n            raise APIError('OKX错误码 '+str(result.get('code'))+'（核对环境、权限、地区与系统时间）')\n",
"        if result.get('code')!='0':\n            code=str(result.get('code') or '')\n            if code=='51603':\n                raise APIError('OKX错误码 51603：订单不存在或暂未可查询',code)\n            raise APIError('OKX错误码 '+code+'（请按具体错误码核对账户/请求参数）',code)\n")
replace_once('exchange.py',
"                raise APIError('OKX订单级错误码 '+str(row['sCode']))\n",
"                raise APIError('OKX订单级错误码 '+str(row['sCode']),str(row['sCode']))\n")
replace_once('exchange.py',
"    def order(self,client_id):\n        return self.get('/api/v5/trade/order',{'instId':INSTRUMENT,'clOrdId':client_id},True)[0]\n",
"    def order(self,client_id='',order_id=''):\n        params={'instId':INSTRUMENT}\n        if order_id:\n            params['ordId']=str(order_id)\n        elif client_id:\n            params['clOrdId']=str(client_id)\n        else:\n            raise APIError('订单查询缺少ordId/clOrdId')\n        rows=self.get('/api/v5/trade/order',params,True)\n        if not rows:\n            raise APIError('OKX错误码 51603：订单不存在或暂未可查询','51603')\n        return rows[0]\n\n    def recent_orders(self):\n        return self.get('/api/v5/trade/orders-history',{'instType':'SWAP','instId':INSTRUMENT,'limit':'100'},True)\n")

# --- app.py: V1.3.5 identity, settings migration, and risk copy ---
replace_all('app.py','V1.3.4','V1.3.5')
replace_all('app.py','KAYTRADE 1.3.4','KAYTRADE 1.3.5')
replace_once('app.py',"previous_path=folder/'settings-v1.3.3.json'","previous_path=folder/'settings-v1.3.4.json'")
replace_once('app.py',
"除连续亏损停开次数外，其余风险数值均可修改并保存；保存时仍执行基本合法性与风险边界校验。",
"除连续亏损停开次数外，其余风险数值均可修改并保存；单笔风险不再设资金5%硬上限，仍保留日回撤、名义仓位/杠杆与基础合法性校验。")
replace_once('app.py',
"基础单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT；评分仓位倍率1×/1.5×/2×，最终仍受日回撤、最大名义仓位和资金5%绝对风险上限约束\\\n",
"基础单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT；评分仓位倍率1×/1.5×/2×，不再设资金5%硬上限，最终仍受日回撤、最大名义仓位与杠杆约束\\\n")

# --- desktop.py: packaged version/smoke-test settings path ---
replace_all('desktop.py','V1.3.4','V1.3.5')
replace_all('desktop.py','1.3.4','1.3.5')

# --- existing tests: old 5% expectations are intentionally obsolete ---
replace_once('test_engine.py',"dict(risk_usdt=6),","")
replace_once('test_engine.py',"    def order(self,cid): return self.ord\n","    def order(self,cid='',order_id=''): return self.ord\n    def recent_orders(self): return []\n")
replace_once('test_v134.py',
"        self.assertLessEqual(high['estimated_loss'],s.capital*.05+1e-9)\n",
"        self.assertLessEqual(high['estimated_loss'],30+1e-9)\n")

print('V1.3.5 patch applied')
