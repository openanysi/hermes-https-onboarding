"""Real Nginx shared TLS site; no server, model, account or public API calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nginx_integration import Fixture, FIXTURE_KEY, free_port
import common
import nginx_shared

NGINX=os.environ.get('NGINX_BINARY')

@unittest.skipUnless(NGINX,'Set NGINX_BINARY for real shared TLS integration')
class SharedNginxIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='openany-shared-nginx-')
        cls.root=Path(cls.temp.name)
        cls.backend=__import__('http.server',fromlist=['ThreadingHTTPServer']).ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        cls.thread=threading.Thread(target=cls.backend.serve_forever,daemon=True);cls.thread.start()
        cls.port=free_port();cls.base='https://127.0.0.1:'+str(cls.port)
        cert,key=cls.root/'cert.pem',cls.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,capture_output=True)
        cls.trust=patch.dict(os.environ,{'SSL_CERT_FILE':str(cert)});cls.trust.start()
        site=f'''server {{
 listen 443 ssl;
 server_name hermes.example.test;
 ssl_certificate {cert};
 ssl_certificate_key {key};
 proxy_intercept_errors on;
 error_page 401 = /dashboard-login;
 location = /dashboard-login {{ return 200 "dashboard login"; }}
 auth_basic "Dashboard";
 auth_basic_user_file {cls.root}/dashboard-users;
 location / {{ return 200 "dashboard remains available"; }}
}}'''
        cls.site=cls.root/'site.conf'
        selected=nginx_shared.select_site({str(cls.site):site})
        cls.site.write_text(nginx_shared.patch_site(site,selected,cls.backend.server_port).replace('listen 443 ssl;',f'listen 127.0.0.1:{cls.port} ssl;'))
        cls.config=cls.root/'nginx.conf'
        cls.config.write_text(f'pid {cls.root}/nginx.pid;\nerror_log {cls.root}/error.log;\nevents {{}}\nhttp {{ access_log off; client_body_temp_path {cls.root}/body; proxy_temp_path {cls.root}/proxy; include {cls.site}; }}\n')
        check=subprocess.run([NGINX,'-p',str(cls.root),'-c',str(cls.config),'-t'],capture_output=True,text=True)
        if check.returncode: raise AssertionError(check.stderr)
        cls.process=subprocess.Popen([NGINX,'-p',str(cls.root),'-c',str(cls.config),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',cls.port),timeout=.1):return
            except OSError:time.sleep(.02)
        raise AssertionError('Shared-site Nginx did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate();cls.process.wait(timeout=5);cls.process.stderr.close()
        cls.backend.shutdown();cls.backend.server_close();cls.thread.join(timeout=5)
        cls.trust.stop();cls.temp.cleanup()

    def opener(self):return urllib.request.build_opener(urllib.request.ProxyHandler({}),common.NoRedirect())

    def test_same_domain_dashboard_is_preserved(self):
        with self.opener().open(self.base+'/',timeout=3) as response:
            self.assertEqual(response.read(),b'dashboard remains available')

    def test_prefixed_discovery_uses_bearer_and_not_inherited_dashboard_auth(self):
        report=common.verify_api(self.base,FIXTURE_KEY,lambda base,path,key=None:common.request_json(base,'/openany'+path,key))
        self.assertTrue(report['authenticated'])
        self.assertEqual(report['skill_count'],1)

    def test_task_headers_body_and_path_reach_cli_unchanged(self):
        payload=b'{"input":"shared TLS fixture","instructions":"load fixture skill"}'
        headers={'Authorization':'Bearer '+FIXTURE_KEY,'Content-Type':'application/json','Idempotency-Key':'fixture-task-id',
                 'X-OpenAny-Task-Id':'fixture-task-id','X-OpenAny-Deadline':'2099-01-01T00:00:00Z','X-OpenAny-Artifact-Delivery':'auto_cli'}
        request=urllib.request.Request(self.base+'/openany/v1/runs',data=payload,headers=headers)
        with self.opener().open(request,timeout=3) as response:self.assertEqual(response.status,202)
        received=Fixture.seen[-1]
        self.assertEqual(received['path'],'/v1/runs')
        self.assertEqual(received['body'],payload)
        self.assertEqual(received['task_id'],'fixture-task-id')
        self.assertEqual(received['idempotency'],'fixture-task-id')
        self.assertEqual(received['delivery'],'auto_cli')
        self.assertEqual(received['deadline'],'2099-01-01T00:00:00Z')

    def test_prefixed_status_and_missing_key(self):
        status,body=common.request_json(self.base,'/openany/v1/runs/run_localfixture',FIXTURE_KEY)
        self.assertEqual(status,200);self.assertEqual(body['status'],'completed')
        status,_=common.request_json(self.base,'/openany/v1/skills')
        self.assertEqual(status,401)

if __name__=='__main__':unittest.main(verbosity=2)
