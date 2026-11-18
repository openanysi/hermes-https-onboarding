import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

class CliDeliveryTests(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT/'scripts/cli_delivery.py').is_file(), 'CLI integration missing')
        return importlib.import_module('cli_delivery')

    def test_current_cli_capability_and_environment_are_checked(self):
        cli=self.module()
        for environment,origin,command in (('prod','https://api.openany.si','openany'),('dev','https://api-dev.openany.si','openany-dev')):
            help='Usage: '+command+' <command>\nBackend gateway: '+origin+'\nseller hermes serve\nseller tasks upload'
            cli.validate_help(environment,help,'v22.22.0')
            for bad in (help.replace('seller hermes serve','unsupported'),help.replace(origin,'https://other.example.com')):
                with self.assertRaises(Exception): cli.validate_help(environment,bad,'v22.22.0')
            with self.assertRaises(Exception): cli.validate_help(environment,help,'v20.19.0')

    def test_private_env_and_user_service_reuse_existing_api_and_seller_keys(self):
        cli=self.module()
        user=importlib.import_module('setup_user')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'hermes';home.mkdir()
            api='fixture-api-key-1234567890'
            (home/'.env').write_text('API_SERVER_KEY='+api+'\n')
            base=user.configure(home,8642,'dev','fixture-dev-seller')
            result=cli.configure(home,'dev',8642,8791,'/node/bin/node','/tools/openany-dev.js',Path(tmp)/'user-units')
            env=user.read_environment(Path(result['environment_file']))
            self.assertEqual(env['OPENANY_DEV_SELLER_KEY'],'fixture-dev-seller')
            self.assertNotIn('OPENANY_SELLER_KEY',env)
            self.assertEqual(env['OPENANY_HERMES_API_KEY'],api)
            self.assertEqual(env['OPENANY_HERMES_URL'],'http://127.0.0.1:8642')
            self.assertEqual(env['OPENANY_TASK_ROOT'],base['task_root'])
            for name in ('OPENANY_HERMES_STATE_DIR','OPENANY_TASK_ROOT'):
                self.assertEqual(stat.S_IMODE(Path(env[name]).stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE(Path(result['environment_file']).stat().st_mode),0o600)
            service=Path(result['unit_file']).read_text()
            self.assertIn('seller hermes serve -j',service)
            self.assertIn('EnvironmentFile='+str(Path(result['environment_file'])).replace('%','%%')+'\n',service)
            self.assertNotIn('EnvironmentFile="',service)
            self.assertNotIn(api,service)
            self.assertNotIn('fixture-dev-seller',json.dumps(result))
            repeated=cli.configure(home,'dev',8642,8791,'/node/bin/node','/tools/openany-dev.js',Path(tmp)/'user-units')
            self.assertFalse(repeated['changed'])
            original=(home/'.env').read_bytes()
            with self.assertRaises(Exception):
                user.configure(home,8642,'prod','fixture-prod-seller')
            self.assertEqual((home/'.env').read_bytes(),original)
            with self.assertRaises(Exception):
                cli.configure(home,'prod',8642,8790,'/node/bin/node','/tools/openany.js',Path(tmp)/'user-units')

    def test_listing_uses_shared_fixed_path_poll_and_auto_cli(self):
        cli=self.module()
        report=cli.listing('hong.example.com','dev',['pdf'],files_required=True)
        self.assertEqual(report['base_url'],'https://hong.example.com')
        self.assertEqual(report['upstream_path'],'/openany/v1/runs')
        for item in report['skills']:
            self.assertEqual(item['async_config']['adapter_type'],'http_json')
            self.assertEqual(item['async_config']['delivery_mode'],'poll')
            self.assertEqual(item['async_config']['output_contract']['artifact_delivery'],'auto_cli')
            self.assertIn(item['skill'],item['fixed_body']['instructions'])
        self.assertEqual(report['upload_origin'],'https://api-dev.openany.si')
        self.assertNotIn('callback_url',json.dumps(report))

    def test_json_only_skill_does_not_require_nonexistent_files(self):
        cli=self.module()
        report=cli.listing('hong.example.com','dev',['space-xhs-title'])
        self.assertEqual(report['skills'][0]['async_config']['output_contract'], {'format':'json'})

    def test_service_command_dollar_signs_are_literal(self):
        cli=self.module()
        self.assertIn('$$',cli.command_quote('/home/a$B/node'))
        self.assertNotIn('$$',cli.quote('/home/a$B/credentials.env'))

    def test_weak_or_nondurable_native_admission_is_not_claimed_ready(self):
        cli=self.module()
        for features in ({},{'runs_idempotency':{'supported':True,'durable':False,'retention_seconds':86400}}):
            with self.assertRaises(Exception): cli.check_capabilities({'features':features})
        self.assertEqual(cli.check_capabilities({'features':{'runs_idempotency':{'supported':True,'durable':True,'retention_seconds':86400}}}),86400)

if __name__=='__main__':unittest.main()
