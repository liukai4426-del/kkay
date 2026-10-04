import argparse, hmac, json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core import Engine, OKX, Error

def main():
    parser=argparse.ArgumentParser(description='kaytrade: human-approved exact plans')
    parser.add_argument('--live',action='store_true',help='User-operated real account; default demo')
    parser.add_argument('--port',type=int,default=8787)
    args=parser.parse_args();token=os.environ.get('KAYTRADE_TOKEN','')
    if len(token)<32: raise SystemExit('Set KAYTRADE_TOKEN to a random value of at least 32 characters')
    root=Path(__file__).parent; runtime=root/'runtime';runtime.mkdir(mode=0o700,exist_ok=True)
    os.chmod(runtime,0o700)
    environment='live' if args.live else 'demo'
    engine=Engine(OKX(environment),runtime/(environment+'.sqlite3'))
    host=f'127.0.0.1:{args.port}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass # Never log token/request bodies.
        def respond(self,status,obj):
            raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.headers.get('Host')!=host:return self.respond(403,{'error':'Host rejected'})
            if self.path!='/':return self.respond(404,{'error':'Not found'})
            raw=(root/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            if self.headers.get('Host')!=host or self.headers.get('Origin') not in (None,'http://'+host):return self.respond(403,{'error':'Origin rejected'})
            if not hmac.compare_digest(self.headers.get('X-Kaytrade-Token',''),token):return self.respond(401,{'error':'Invalid token'})
            if self.headers.get('Content-Type')!='application/json':return self.respond(400,{'error':'JSON required'})
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=65536:raise Error('Invalid request length')
                body=json.loads(self.rfile.read(n))
                if self.path=='/api/import':result=engine.import_plan(body)
                elif self.path=='/api/preview':result=engine.preview(body['id'])
                elif self.path=='/api/confirm':
                    if body.get('ack')!='CONFIRM EXACT PLAN AND EXPIRY CANCEL':raise Error('Explicit confirmation required')
                    result=engine.confirm(body['id'],body['approval'])
                elif self.path=='/api/state':result=engine.state()
                elif self.path=='/api/reconcile':result=engine.reconcile()
                else:return self.respond(404,{'error':'Not found'})
                self.respond(200,result)
            except (Error,ValueError,KeyError,TypeError) as e:self.respond(400,{'error':str(e)[:300]})
            except Exception:self.respond(500,{'error':'Internal failure; inspect local state, do not blindly retry'})
    def monitor():
        # Executes only expiry cancellation of orders approved in this process/database.
        while True:
            time.sleep(30)
            try:engine.reconcile()
            except Exception:engine.event('monitor_attention',{'message':'Reconciliation failed; manual inspection required'})
    threading.Thread(target=monitor,daemon=True).start()
    print(f'kaytrade {environment.upper()} http://{host} — no orders before explicit UI confirmation',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
if __name__=='__main__':main()
