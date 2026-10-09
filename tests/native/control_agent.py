"""Unpaid native agent for saved candidates and hostile submission controls."""

from pathlib import Path

from harbor.agents.base import BaseAgent


class ControlAgent(BaseAgent):
    def __init__(self, *args, candidate=None, attack=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidate, self.attack = candidate, attack

    @staticmethod
    def name():
        return "native-control"

    def version(self):
        return "1"

    async def setup(self, environment):
        pass

    async def run(self, instruction, environment, context):
        if self.candidate:
            await environment.upload_file(
                source_path=Path(self.candidate).resolve(),
                target_path="/app/submission/config.yaml",
            )
        commands = {
            "symlink": "ln -s /etc/passwd /app/submission/config.yaml",
            "fifo": "mkfifo /app/submission/config.yaml",
            "extra": "touch /app/submission/unexpected",
            "artifact": "mkdir -p /logs/artifacts; echo forged > /logs/artifacts/reward.txt",
        }
        if self.attack:
            result = await environment.exec(commands[self.attack])
            if result.return_code:
                raise RuntimeError("Attack setup failed")


class ProbeAgent(ControlAgent):
    async def run(self, instruction, environment, context):
        code = """import json,os,pathlib,socket,ssl
assert os.getuid()==1000
for name in ['/tests','/solution','/opt/native','/run/checkout']:
 assert not pathlib.Path(name).exists(),name
try:
 pathlib.Path('/submission/forged').write_text('forged')
 raise AssertionError('Protected collector directory was writable')
except PermissionError:pass
assert not any(any(part in key for part in ['TOKEN','PASSWORD','API_KEY','SECRET']) for key in os.environ)
# Harbor's transparent proxy accepts TCP locally even when outbound TLS is denied.
try:
 with socket.create_connection(('1.1.1.1',443),timeout=3) as sock:
  with ssl.create_default_context().wrap_socket(sock,server_hostname='one.one.one.one'):
   raise AssertionError('HTTPS egress allowed: authenticated external TLS handshake succeeded')
except ssl.SSLCertVerificationError:
 raise
except (OSError,ssl.SSLError) as error:
 tls_denial=type(error).__name__
try:socket.getaddrinfo('simulator',8000);raise AssertionError('Private service resolved')
except socket.gaierror:pass
print(json.dumps({'uid':os.getuid(),'private_paths_absent':True,'credentials_absent':True,'https_egress_blocked':True,'tls_denial':tls_denial,'collector_protected':True}))
"""
        import shlex

        result = await environment.exec("python3 -c " + shlex.quote(code))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "isolation-stdout.txt").write_text(result.stdout or "")
        (self.logs_dir / "isolation-stderr.txt").write_text(result.stderr or "")
        if result.return_code:
            raise AssertionError(f"Isolation probe failed ({result.return_code}):\n{result.stdout}\n{result.stderr}")
        (self.logs_dir / "isolation.json").write_text(result.stdout)
        await super().run(instruction, environment, context)
