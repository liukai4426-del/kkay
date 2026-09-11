import unittest

import exchange
import v141_exit_limit_patch as patch


class FakePostTarget:
    def __init__(self):
        self.calls=[]
    def request(self,method,path,params=None,private=False):
        self.calls.append((method,path,params,private))
        return []


class ExitLimitTests(unittest.TestCase):
    def test_combined_bracket_uses_explicit_limit_prices(self):
        body={
            'instId':'BTC-USDT-SWAP','ordType':'limit','px':'100','sz':'1',
            'attachAlgoOrds':[
                {'attachAlgoClOrdId':'br1','tpTriggerPx':'120','tpOrdPx':'-1','tpTriggerPxType':'last'},
                {'attachAlgoClOrdId':'br1','slTriggerPx':'90','slOrdPx':'-1','slTriggerPxType':'last'},
            ],
        }
        out=patch._limit_attached_payload('/api/v5/trade/order',body)
        self.assertEqual(len(out['attachAlgoOrds']),1)
        bracket=out['attachAlgoOrds'][0]
        self.assertEqual(bracket['tpOrdKind'],'condition')
        self.assertEqual(bracket['tpTriggerPx'],'120')
        self.assertEqual(bracket['tpOrdPx'],'120')
        self.assertEqual(bracket['slTriggerPx'],'90')
        self.assertEqual(bracket['slOrdPx'],'90')
        self.assertEqual(body['attachAlgoOrds'][0]['tpOrdPx'],'-1')

    def test_live_limit_bracket_is_accepted_by_preserved_validator_only_when_exact(self):
        rows=[{'tpTriggerPx':'120','tpOrdPx':'120','slTriggerPx':'90','slOrdPx':'90'}]
        converted=patch._algos_for_legacy_validator(rows)
        self.assertEqual(converted[0]['tpOrdPx'],'-1')
        self.assertEqual(converted[0]['slOrdPx'],'-1')
        mismatch=[{'tpTriggerPx':'120','tpOrdPx':'119.5','slTriggerPx':'90','slOrdPx':'90'}]
        converted=patch._algos_for_legacy_validator(mismatch)
        self.assertEqual(converted[0]['tpOrdPx'],'119.5')
        self.assertEqual(converted[0]['slOrdPx'],'-1')

    def test_exchange_post_sends_limit_tp_sl_to_request(self):
        fake=FakePostTarget()
        body={'attachAlgoOrds':[{'attachAlgoClOrdId':'br2','tpTriggerPx':'110','tpOrdPx':'-1','slTriggerPx':'95','slOrdPx':'-1'}]}
        exchange.Exchange.post(fake,'/api/v5/trade/order',body)
        method,path,payload,private=fake.calls[-1]
        self.assertEqual((method,path,private),('POST','/api/v5/trade/order',True))
        bracket=payload['attachAlgoOrds'][0]
        self.assertEqual(bracket['tpOrdPx'],'110')
        self.assertEqual(bracket['slOrdPx'],'95')
        self.assertNotEqual(bracket['tpOrdPx'],'-1')
        self.assertNotEqual(bracket['slOrdPx'],'-1')

    def test_triggered_exit_child_is_recognized_strictly(self):
        p={'posSide':'long','legs':[{'state':'filled','sz':'2','tp':'120','sl':'90'}]}
        child={'source':'7','posSide':'long','tdMode':'isolated','side':'sell','ordType':'limit','px':'90','sz':'2'}
        self.assertIsNotNone(patch._matching_leg_for_exit_child(child,p))
        foreign=dict(child,px='91')
        self.assertIsNone(patch._matching_leg_for_exit_child(foreign,p))
        wrong_source=dict(child,source='')
        self.assertIsNone(patch._matching_leg_for_exit_child(wrong_source,p))


if __name__=='__main__':
    unittest.main()
