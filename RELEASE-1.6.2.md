# KAYTRADE V1.6.2 Build1620

- All 5m BOLL middle/outer entry paths require 4H trend aligned.
- Outer-band 2x single LIMIT sizing removed; all initial BOLL entries use 1x base sizing.
- +1R break-even stop amendment disabled; original 1H ATR stop remains until SL or 2R full TP.
- Opening records explicitly log LONG/SHORT direction, BOLL path, score, LIMIT price, notional and order id.
- OKX 51290 bot-engine-upgrade response soft-pauses only new entries with 5/10/15/30s backoff and auto recovery; existing position/protection management continues.
