# 认证与接入说明

## 两个入口

根目录 `app.py` 是 Tkinter 原生桌面程序；新增 `kaytrade/app.py` 是本机网页确认执行程序。网页版从 `kaytrade` 目录启动，两者分别运行，不共享账户状态或交易授权。不要并行管理同一账户。新增入口不改变桌面策略，不接入聊天自动交易任务，也不启动交易所操作。

## 桌面认证组件与请求流程

`app.App.connect` 从遮掩输入框读取 API Key、Secret Key、Passphrase，交给后台队列；`App.worker` 创建 `Exchange` 与 `Engine`。`Engine.connect` 同步交易所时间、读取账户UID、余额、仓位和订单，完成只读连接检查。连接不等于自动交易授权：`App.arm` 另外要求输入 DEMO 或 LIVE 并校验设置。

`exchange.Exchange.request` 对私有请求计算 Base64(HMAC-SHA256(Secret, timestamp + method + target + body))，其中 target 包含GET查询参数，body是实际发送的紧凑JSON。请求头携带 OK-ACCESS-KEY、OK-ACCESS-SIGN、OK-ACCESS-TIMESTAMP、OK-ACCESS-PASSPHRASE；Secret本身不发出。模拟/实盘使用 x-simulated-trading=1/0。HTTPS传输，禁用代理与重定向，POST只允许订单和设置杠杆路径；域名由客户端白名单限制。网络异常不自动重发写入。

凭据保存在输入框和进程对象内存中，不写入 settings.json 或订单状态。账户状态文件名使用域名、环境与UID的SHA256摘要，不是认证令牌。程序没有OAuth、JWT、登录会话或刷新令牌；DEMO/LIVE只是启动授权文字。`desktop.py` 为打包程序配置CA证书。进程内存不等于加密保险库，退出后重新输入凭据。

## 网页认证组件与请求流程

`kaytrade/index.html` 从密码框读取本地访问令牌，以 X-Kaytrade-Token 发送到127.0.0.1服务；不使用localStorage/cookie。`kaytrade/app.py` 要求环境变量 KAYTRADE_TOKEN 长度至少32，使用hmac.compare_digest核对，并限制Host与Origin、JSON类型和请求长度。首页无需令牌，所有API POST均需令牌。该令牌是共享访问凭据，非JWT；程序不自动轮换或刷新，轮换须更新环境变量并重启。

浏览器导入JSON → Engine.import_plan/preview读取账户和成本 → 用户确认60秒有效预览 → Engine.confirm保存意图 → OKX.request签名并发送HTTPS请求 → reconcile查询订单状态。approval是预览SHA256摘要，绑定确认版本，不替代访问令牌或用户确认。请求受理不代表成交；超时先核实原客户端订单号。

网页版OKX凭据从 OKX_API_KEY、OKX_SECRET_KEY、OKX_PASSPHRASE 环境变量读取，仅服务器进程持有，不下发页面、不写SQLite；demo/live数据库分开。模拟盘添加x-simulated-trading=1，--live选择实盘连接。此选择不是本聊天实盘交易授权。环境变量文件不会自动加载。

## 接入范围与验证

新增kaytrade源码、示例配置和假API测试；不上传密钥、运行数据库或交易日志。现有桌面版采用双向持仓与不同杠杆/执行规则，网页入口只支持net_mode、逐仓20倍和逐笔确认；不互换状态文件。网页部分成交保护尚需人工核实，未实现完整分档保护管理。12项离线单元测试通过，未进行交易所端到端验证。

代码依据：`app.py`、`engine.py`、`exchange.py`、`desktop.py`、`README-中文.md`与`kaytrade/app.py`、`kaytrade/core.py`、`kaytrade/index.html`、`kaytrade/README.md`。

