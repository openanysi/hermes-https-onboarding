import importlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

SITE = '''# retain dashboard and certificate
server {
    listen 80;
    server_name hong.example.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name hong.example.com;
    ssl_certificate /cert/existing.pem;
    ssl_certificate_key /cert/existing.key;
    auth_basic "Dashboard";
    location / { proxy_pass http://127.0.0.1:9119; }
}
'''

class SharedHttpsTests(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT / 'scripts/nginx_shared.py').is_file(), 'shared HTTPS implementation missing')
        return importlib.import_module('nginx_shared')

    def test_existing_tls_site_is_discovered_without_new_domain_or_certificate(self):
        shared = self.module()
        site = shared.select_site({'/etc/nginx/sites-available/hermes': SITE})
        self.assertEqual(site['domain'], 'hong.example.com')
        self.assertEqual(site['path'], '/etc/nginx/sites-available/hermes')
        updated = shared.patch_site(SITE, site, 8790)
        self.assertIn('ssl_certificate /cert/existing.pem;', updated)
        self.assertIn('location / { proxy_pass http://127.0.0.1:9119; }', updated)
        self.assertIn('location ^~ /openany/', updated)
        self.assertIn('proxy_pass http://127.0.0.1:8790/;', updated)
        self.assertIn('auth_basic off;', updated)
        self.assertEqual(updated.split('server {')[1], SITE.split('server {')[1])
        again = shared.select_site({'/etc/nginx/sites-available/hermes': updated})
        self.assertEqual(updated, shared.patch_site(updated, again, 8790))

    def test_ambiguous_existing_hosts_require_selection_not_new_dns(self):
        shared = self.module()
        sources = {'/etc/nginx/a': SITE, '/etc/nginx/b': SITE.replace('hong.example.com','other.example.com')}
        with self.assertRaisesRegex(Exception, 'domain'):
            shared.select_site(sources)
        self.assertEqual(shared.select_site(sources, 'other.example.com')['path'], '/etc/nginx/b')

    def test_existing_namespace_or_server_rewrite_is_not_overwritten(self):
        shared = self.module()
        for extra in ('location /openany/v1/ { return 200; }', 'return 302 /login;', 'rewrite ^ /login;', 'if ($redirect) { return 302 /login; }'):
            with self.subTest(extra=extra), self.assertRaises(Exception):
                shared.select_site({'/etc/nginx/a': SITE.replace('    auth_basic', '    '+extra+'\n    auth_basic')})

    def test_included_namespace_is_detected(self):
        shared = self.module()
        source = SITE.replace('    auth_basic', '    include /etc/nginx/custom.conf;\n    auth_basic')
        with self.assertRaises(Exception):
            shared.select_site({'/etc/nginx/a': source, '/etc/nginx/custom.conf': 'location = /openany/v1/runs { return 200; }'})

    def test_comments_quoted_braces_and_regex_do_not_break_dashboard_site(self):
        shared = self.module()
        source = SITE.replace('    auth_basic', '    # ignored } server {\n    add_header X-Example "brace { and }";\n    location ~ "^/old/[0-9]{2}$" { return 404; }\n    auth_basic')
        site = shared.select_site({'/etc/nginx/a': source})
        self.assertIn('location ^~ /openany/', shared.patch_site(source, site, 8790))

    def test_failed_nginx_validation_restores_existing_site(self):
        shared = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'site.conf'; path.write_text(SITE)
            site = shared.select_site({str(path): SITE})
            calls = []
            def runner(args):
                calls.append(args)
                if args == ['nginx','-t'] and len(calls) == 1:
                    raise shared.Failure('fixture invalid')
                return ''
            with self.assertRaises(shared.Failure):
                shared.apply_site(site, 8790, Path(tmp)/'backups', runner)
            self.assertEqual(path.read_text(), SITE)
            self.assertGreaterEqual(len(calls), 2)

    def test_failed_authenticated_verification_restores_dashboard_site(self):
        shared = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'site.conf';path.write_text(SITE)
            site=shared.select_site({str(path):SITE})
            def verify(): raise shared.Failure('fixture authenticated discovery failed')
            with self.assertRaises(shared.Failure):
                shared.apply_site(site,8790,Path(tmp)/'backups',lambda args:'',verify)
            self.assertEqual(path.read_text(),SITE)

    def test_inherited_auth_request_is_disabled_only_for_managed_api(self):
        shared = self.module()
        source=SITE.replace('    auth_basic','    auth_request /dashboard-auth;\n    auth_basic')
        site=shared.select_site({'/etc/nginx/a':source})
        result=shared.patch_site(source,site,8790)
        self.assertIn('auth_request /dashboard-auth;',result)
        self.assertIn('auth_request off;',result)

    def test_sites_enabled_symlink_is_preserved_when_editing_actual_site(self):
        shared = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            actual=Path(tmp)/'available.conf';actual.write_text(SITE)
            link=Path(tmp)/'enabled.conf';link.symlink_to(actual)
            sources=shared.read_sources('# configuration file '+str(link)+':\n')
            selected=shared.select_site(sources)
            self.assertEqual(selected['path'],str(actual.resolve()))
            shared.apply_site(selected,8790,Path(tmp)/'backups',lambda args:'')
            self.assertTrue(link.is_symlink())
            self.assertIn('location ^~ /openany/',actual.read_text())

    def test_changed_site_since_preflight_is_refused(self):
        shared = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'site.conf'; path.write_text(SITE)
            site=shared.select_site({str(path):SITE})
            path.write_text(SITE+'# operator changed it\n')
            with self.assertRaises(shared.Failure):
                shared.apply_site(site,8790,Path(tmp)/'backups',lambda args:'')
            self.assertTrue(path.read_text().endswith('# operator changed it\n'))

if __name__ == '__main__': unittest.main()
