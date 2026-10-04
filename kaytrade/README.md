# kaytrade

本地网页确认式 OKX BTC-USDT-SWAP 执行原型。Python 3.11+，只用标准库。GitHub 保存代码和测试；密钥和程序运行在用户自己的机器。默认模拟盘，不读取父项目的旧订单身份或配置，不改变原任务。

## 启动

在本目录运行，先设置环境变量（真实值不能提交GitHub）：

```sh
export OKX_API_KEY='your-demo-key'
export OKX_SECRET_KEY='your-demo-secret'
export OKX_PASSPHRASE='your-passphrase'
export KAYTRADE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 app.py
```

打开 http://127.0.0.1:8787 ，将本地令牌填入页面。令牌不会写到浏览器存储。用户独立部署时 `python3 app.py --live` 选择实盘，必须使用对应实盘凭据；本项目开发过程中没有启动实盘执行。

API权限需要Read和Trade，不要Withdraw，建议绑定IP。Trade权限还包含部分账户操作；程序只实现必要订单和杠杆接口。当前仅支持net_mode，逐仓20倍，不自动改账户仓位模式。地区接口域名当前固定www.okx.com；其他地区未支持。

## 操作

1. 从策略分析取得完整JSON（格式见 `example_plan.json`），使用新的ID和带时区的确认截止时间；示例是虚构离线演示，已过期，不能用于市场下单。
2. 导入后程序只读核实账户空仓、无挂单、规格、费用、盘口和余额。未知字段、跨盘口、数量/价格步长不对、净RR低于1.2都会拒绝。不解析自然语言，不自动豁免约束。
3. 页面展示最终参数、环境、账号UID、成本。勾选确认并点击提交，授权此版本及其期限到期撤单。预览60秒失效。参数变化用新ID重新确认；重复确认不能重复下单。
4. 程序先保存确认及订单意图到SQLite，再调用OKX。超时/部分拒绝停止后续提交，显示needs_attention，不自动重发。
5. 本地进程每30秒核实已授权订单，到期撤未成交部分并查询终态；关机、网络中断不保证撤单准时。人工复核按钮随时可用。普通挂单时长与确认截止时间独立。

每档<=1500U，总计<=3000U。500U实验使用相应张数，不自动设置额度例外。价格TP须2R，maker入场/taker退出费用+逐档资金费假设后净RR>=1.2。支持60..10800秒期限，但确认页面会完整展示。程序不生成交易策略、不宣称胜率，不设置PEE自动退出。

## 当前重要限制

这是可测试的执行原型，不是完整无人值守风险管理系统。附带TP/SL发送给OKX，但不能据此认定部分成交已受保护。任何成交均产生protection_attention事件，需要在OKX核查实际保护；第一档核实已成交时停止提交第二档。发生竞态仍可能两档成交。未实现自动补建部分成交保护、独立分档平仓账务、WebSocket保护状态监听或PEE执行。这些没有验证前不适合无人值守实盘运行。

目前不提供手动改单/PEE/平仓按钮；修改价格须新版本，已有订单会阻止新组提交。不会自动收紧止损。原项目的周日休息/一次性豁免和自动任务不接入本程序：用户每次确认具体方案，但仍需核对自己的策略约束。

SQLite `runtime/demo.sqlite3` 与 `runtime/live.sqlite3` 分开，包含审计记录；保护日志注意私密性。API凭据不写入数据库。不可删除数据库后重复同一计划，否则无法依靠本地历史防重。日志不是账务最终净盈亏报表，资金费和实际成交手续费需后续账务核实。

## 测试与GitHub

```sh
python3 -m unittest discover -s tests -v
```

测试只调用假API，不访问交易所。根目录 `.github/workflows/kaytrade-tests.yml` 在GitHub运行相同测试。推荐私有仓库，确认仓库内容后用GitHub CLI创建并推送；不要推送runtime或密钥文件。

接口依据：https://www.okx.com/docs-v5/en/ 。采用REST签名、clOrdId、attachAlgoOrds。API受理不代表成交。

## 与桌面入口的关系

从本目录运行 `python3 app.py`。仓库根目录仍是原生桌面程序，两者不共享凭据、状态或启动授权，不要同时管理同一账户。认证流程见 [认证与接入说明](../AUTHENTICATION.md)。本入口未接入聊天自动交易任务。
