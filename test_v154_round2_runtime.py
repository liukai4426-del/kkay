import unittest
from types import SimpleNamespace
from unittest.mock import patch

import v138_strategy_patch as v138
import v154_record_card_boll_fix as fix


class FakeStore:
    def __init__(self):
        self.data={'last_bar':None,'active':None}
        self.saved=0
        self.records=[]
    def save(self): self.saved+=1
    def record(self,kind,data): self.records.append((kind,data))


class FakeExchange:
    def __init__(self): self.posts=[]
    def post(self,path,body):
        self.posts.append((path,body)); return [{'ordId':'654321'}]


class V154Round2RuntimeTests(unittest.TestCase):
    def test_valid_old_boll_opportunity_can_submit_limit_later(self):
        store=FakeStore(); exchange=FakeExchange(); emitted=[]
        fake=SimpleNamespace(
            emit=lambda kind,data: emitted.append((kind,data)),
            enabled=True,
            _verify_latest=lambda *args:None,
            store=store,
            x=exchange,
        )
        # Signal closed many minutes ago.  Final V1.5.4 must not reject it just
        # because one wall-clock minute elapsed.
        market={
            'side':'做多','bar':2_000_000,
            'opportunity':{'id':'b154-test','signal_close_ms':1_000_000},
        }
        score={'total':6.0,'opportunity':market['opportunity']}
        plan={'exchange_side':'buy','posSide':'long','px':'100.0','sz':'1','tp':'102.0','sl':'99.0','side':'做多'}
        prepared=(plan,1,1.0,'开仓信号')
        with patch.object(v138,'_prepare_order',return_value=prepared), \
             patch.object(v138,'_set_leverage',return_value=True):
            fix._submit_initial_limit_persistent(fake,market,score,10000.0,10000.0,100.0)
        self.assertEqual(len(exchange.posts),1)
        path,body=exchange.posts[0]
        self.assertEqual(path,'/api/v5/trade/order')
        self.assertEqual(body['ordType'],'limit')
        self.assertEqual(body['px'],'100.0')
        active=store.data['active']
        self.assertEqual(active['build'],'154.2')
        self.assertFalse(active['boll_time_window_enabled'])
        self.assertEqual(active['signal_close_ms'],1_000_000)
        self.assertIn('不设BOLL触发后1分钟截止',emitted[-2][1])

    def test_missing_boll_opportunity_is_still_blocked(self):
        store=FakeStore(); exchange=FakeExchange(); emitted=[]
        fake=SimpleNamespace(
            emit=lambda kind,data: emitted.append((kind,data)),enabled=True,
            _verify_latest=lambda *args:None,store=store,x=exchange,
        )
        fix._submit_initial_limit_persistent(fake,{'side':'做多','bar':1},{'total':6.0},100,100,10)
        self.assertFalse(exchange.posts)
        self.assertIn('缺少有效5m BOLL机会状态',emitted[-1][1])


if __name__=='__main__':
    unittest.main()
