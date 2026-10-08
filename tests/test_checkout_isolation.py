"""Adversarial checkout credentials, receipt replay and fresh-attempt isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from sapi_config_lab.contracts import CompiledWorkflow, CompileOptions, RunBinding
from sapi_config_lab.execute import n8n
from tests import test_checkout_package_environment as support
from tests.support.pinned import AVAILABLE

SERVER, HOOKS = support.SERVER, support.HOOKS

# Run inside a fresh verifier before its semantic workflow window is prepared.
# Only digests/statuses leave the container; credentials never enter argv or stdout.
DOCKER_CANDIDATE_PROBE = r"""
import hashlib,json,os,pathlib,urllib.error,urllib.request
credentials=json.loads(pathlib.Path('/run/checkout/credentials.json').read_text())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
checks={}
for route in ('/prepare','/snapshot','/admin/finalize'):
    request=urllib.request.Request('http://simulator:8000'+route,data=b'{}',headers={'Authorization':'Bearer '+credentials['tool_token']})
    try:
        with opener.open(request,timeout=5) as response: checks[route]=response.status
    except urllib.error.HTTPError as error:
        checks[route]=error.code
        error.close()
assert checks == {'/prepare':401,'/snapshot':401,'/admin/finalize':404},checks
assert all(secret not in json.dumps(dict(os.environ)) for secret in credentials.values())
print(json.dumps({'candidate_denials':checks,'credential_identity':{key:hashlib.sha256(value.encode()).hexdigest() for key,value in credentials.items()}}))
"""


DOCKER_REDIRECT_PROBE = r"""
import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
from payload.environment import hooks
forwarded=[]
status=[302]
class Sink(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        forwarded.append(self.headers.get('Authorization'))
        self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    do_POST=do_GET
sink=ThreadingHTTPServer(('127.0.0.1',0),Sink)
class Redirect(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        self.send_response(status[0]);self.send_header('Location','http://127.0.0.1:'+str(sink.server_port)+'/capture');self.end_headers()
redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in (sink,redirect)]
for thread in threads: thread.start()
observed=[]
try:
    context={'options':{'world_url':'http://127.0.0.1:'+str(redirect.server_port)}}
    for code in (301,302,303,307,308):
        status[0]=code
        try: hooks._post(context,'/prepare')
        except HTTPError as error:
            observed.append(error.code);error.close()
    assert observed==[301,302,303,307,308],observed
    assert not forwarded
    print(json.dumps({'redirect_statuses':observed,'forwarded_credentials':len(forwarded)}))
finally:
    for server in (sink,redirect): server.shutdown();server.server_close()
    for thread in threads: thread.join(timeout=2)
"""

# Invoke once per fresh, isolated verifier/world attempt instead of the normal worker.
DOCKER_FRESH_RETRY_PROBE = r"""
import concurrent.futures,hashlib,json,pathlib,urllib.request
credentials=json.loads(pathlib.Path('/run/checkout/credentials.json').read_text())
def post(route,data,key):
    request=urllib.request.Request('http://simulator:8000'+route,data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+credentials[key]})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response: return json.load(response)
window=post('/prepare',{},'admin_token')
initial=post('/tools',{'operation':'source.read','arguments':{},'operation_id':'read-initial'},'tool_token')
assert 'charge_card(currency, amount)' in initial['value']['content']
request={'operation':'checkout.patch','arguments':{'old':'charge_card(currency, amount)','new':'charge_card(amount, currency)'},'operation_id':'same-mutation'}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    receipts=list(pool.map(lambda _:post('/tools',request,'tool_token'),range(4)))
assert receipts[0]['ok'] and receipts==[receipts[0]]*4
frozen=post('/snapshot',{},'admin_token')
assert frozen['environment']['tool_calls']==2
assert len(frozen['environment']['final']['patches'])==1
assert frozen==post('/snapshot',{},'admin_token')
assert all(secret not in json.dumps(frozen) for secret in credentials.values())
print(json.dumps({'run_id':window['run_id'],'initial_sha256':hashlib.sha256(json.dumps(frozen['environment']['initial'],sort_keys=True).encode()).hexdigest(),'tool_calls':frozen['environment']['tool_calls'],'patches':len(frozen['environment']['final']['patches']),'credential_identity':{key:hashlib.sha256(value.encode()).hexdigest() for key,value in credentials.items()}}))
"""


class CheckoutCredentialBoundaryTests(unittest.TestCase):
    def test_n8n_receives_only_tool_binding_and_persists_no_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            tool, admin, host = "tool-sentinel-06", "admin-sentinel-06", "host-sentinel-06"
            compiled = CompiledWorkflow(
                "n8n",
                {"id": "workflow", "nodes": [], "connections": {}},
                {},
                30,
                CompileOptions(operation_url="http://simulator:8000/tools"),
            )
            with (
                patch.dict("os.environ", {"AWB_ENV_ADMIN_TOKEN": admin, "HOST_SECRET": host}),
                patch.object(n8n, "version", return_value=n8n.PINNED_N8N_VERSION),
                patch.object(n8n, "process", return_value=(1, "deliberate import refusal", "")) as process,
            ):
                record = n8n.execute_compiled(compiled, target, RunBinding(operation_token=tool))
            self.assertEqual(process.call_count, 1)
            environment = process.call_args.args[1]
            self.assertEqual(environment["SAPI_OPERATION_TOKEN"], tool)
            self.assertNotIn(admin, json.dumps(environment))
            self.assertNotIn(host, json.dumps(environment))
            persisted = json.dumps(record).encode() + b"".join(p.read_bytes() for p in target.rglob("*") if p.is_file())
            for secret in (tool, admin, host):
                self.assertNotIn(secret.encode(), persisted)

    def test_admin_redirects_never_reach_another_listener(self):
        forwarded = []
        code = [302]

        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                forwarded.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = do_GET

        sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.send_response(code[0])
                self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/stolen")
                self.end_headers()

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (sink, redirect)]
        for thread in threads:
            thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                credentials = Path(temporary) / "credentials.json"
                credentials.write_text(json.dumps({"admin_token": "private-admin-06"}))
                context = {
                    "options": {
                        "world_url": f"http://127.0.0.1:{redirect.server_port}",
                        "world_credentials": str(credentials),
                    }
                }
                for status in (301, 302, 303, 307, 308):
                    code[0] = status
                    with self.subTest(status=status), self.assertRaises(HTTPError) as raised:
                        HOOKS._post(context, "/prepare")
                    self.assertEqual(raised.exception.code, status)
                self.assertEqual(forwarded, [])
        finally:
            for server in (sink, redirect):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)


@unittest.skipUnless(AVAILABLE, "Requires verified external AutoWFBench checkout and benchmark extra")
class CheckoutAttemptIsolationTests(unittest.TestCase):
    setUp = support.CheckoutNativeWorldTests.setUp
    close = support.CheckoutNativeWorldTests.close
    post = support.CheckoutNativeWorldTests.post

    def test_non_ascii_authorization_is_denied_without_server_traceback(self):
        errors = io.StringIO()
        with redirect_stderr(errors):
            for route in ("/prepare", "/snapshot", "/tools"):
                with self.subTest(route=route), self.assertRaises(HTTPError) as raised:
                    self.post(route, {}, "non-ascii-\u00e9")
                self.assertEqual(raised.exception.code, 401)
        self.assertEqual(errors.getvalue(), "")
        self.assertIsNone(self.world.started)
        self.assertEqual(self.world.world.calls, 0)

    def test_tools_cannot_proxy_admin_routes_or_read_environment_secrets(self):
        binding = HOOKS.prepare(self.context)
        for operation in ("/snapshot", "../admin/finalize", "http://simulator:8000/snapshot", "os.environ", "__dict__"):
            receipt = self.post("/tools", {"operation": operation, "arguments": {}}, binding.operation_token)
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["error"]["code"], "UNKNOWN_TOOL")
            self.assertNotIn(self.world._admin_token, json.dumps(receipt))
        self.assertFalse(self.world._frozen)
        for route in ("/prepare", "/snapshot"):
            with self.assertRaises(HTTPError) as raised:
                self.post(route, {}, binding.operation_token)
            self.assertEqual(raised.exception.code, 401)

    def test_concurrent_retries_dispatch_once_and_new_world_rejects_old_credentials(self):
        binding = HOOKS.prepare(self.context)
        request = {
            "operation": "checkout.patch",
            "arguments": {"old": "charge_card(currency, amount)", "new": "charge_card(amount, currency)"},
            "operation_id": "same-logical-operation",
        }
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(lambda _: self.post("/tools", request, binding.operation_token), range(6)))
        self.assertTrue(receipts[0]["ok"])
        self.assertEqual(receipts, [receipts[0]] * 6)
        self.assertEqual(self.world.world.calls, 1)
        self.assertEqual(len(self.world.world.state["patches"]), 1)
        fresh = SERVER.World(self.upstream.ChallengeEnvironment(self.package, 0), self.package["definition"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.handler_for(fresh))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            old = Request(
                f"http://127.0.0.1:{server.server_port}/tools",
                data=json.dumps(request).encode(),
                headers={"Authorization": "Bearer " + binding.operation_token},
            )
            with self.assertRaises(HTTPError) as raised:
                try:
                    build_opener(ProxyHandler({})).open(old, timeout=5)
                except HTTPError as error:
                    error.close()
                    raise
            self.assertEqual(raised.exception.code, 401)
            self.assertEqual(fresh.world.calls, 0)
            fresh.prepare()
            result = fresh.call("source.read", operation_id="same-logical-operation")
            self.assertTrue(result["ok"])
            self.assertIn("charge_card(currency, amount)", result["value"]["content"])
            self.assertEqual(fresh.world.calls, 1)
            self.assertNotEqual(fresh.window["run_id"], self.world.window["run_id"])
            self.assertNotEqual(fresh._admin_token, self.world._admin_token)
        finally:
            fresh.snapshot()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_echoed_run_credentials_are_redacted_without_changing_request_identity(self):
        self.world.prepare()
        arguments = {"ignored": self.world._candidate_token}
        request = {"operation": "source.read", "arguments": arguments, "max_attempts": 1}
        expected = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        receipt = self.world.call("source.read", arguments, operation_id=self.world._candidate_token)
        self.assertTrue(receipt["ok"])
        self.assertEqual(self.world.call("source.read", arguments, operation_id=self.world._candidate_token), receipt)
        frozen = self.world.snapshot()
        self.assertEqual(self.world.world.calls, 1)
        self.assertEqual(frozen["transport"]["events"][0]["request_digest"], expected)
        for secret in (self.world._candidate_token, self.world._admin_token):
            self.assertNotIn(secret, json.dumps(frozen))
