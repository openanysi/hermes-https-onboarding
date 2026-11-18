"""Exercise the real Linux systemd EnvironmentFile parser; never start a service."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import setup_user
import cli_delivery

@unittest.skipUnless(sys.platform=='linux' and shutil.which('systemd-analyze'),'Requires Linux systemd-analyze')
class SystemdEnvironmentIntegration(unittest.TestCase):
    def verify(self,root,content):
        unit=Path(root)/'openany-environment-parser-test.service'
        unit.write_text(content)
        result=subprocess.run(['systemd-analyze','verify',str(unit)],capture_output=True,text=True,timeout=30)
        # The host may report unrelated installed-unit warnings while resolving targets.
        diagnostics='\n'.join(line for line in result.stderr.splitlines() if unit.name in line)
        return result.returncode,diagnostics

    def test_old_quoted_environment_file_is_reproduced_as_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            body='[Service]\nType=oneshot\nExecStart=/usr/bin/true\nEnvironmentFile="'+tmp+'/fixture.env"\n'
            _,diagnostics=self.verify(tmp,body)
            self.assertIn('EnvironmentFile= path is not absolute',diagnostics)

    def test_gateway_dropin_literal_path_with_spaces_percent_and_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'100% space 中文'/ 'fixture.env'
            env.parent.mkdir();env.write_text('OPENANY_FIXTURE="literal"\n')
            body='[Service]\nType=oneshot\nExecStart=/usr/bin/true\n'+setup_user.dropin_text(env)
            code,diagnostics=self.verify(tmp,body)
            self.assertEqual(code,0,diagnostics)
            self.assertNotIn('EnvironmentFile= path is not absolute',diagnostics)
            self.assertNotIn('Failed to resolve unit specifiers',diagnostics)

    def test_generated_cli_service_environment_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'profile with spaces 中文';home.mkdir()
            setup_user.configure(home,8642,'prod','fixture-systemd-seller-token')
            result=cli_delivery.configure(home,'prod',8642,8790,sys.executable,'/usr/bin/true',Path(tmp)/'units')
            code,diagnostics=self.verify(tmp,Path(result['unit_file']).read_text())
            self.assertEqual(code,0,diagnostics)
            self.assertNotIn('EnvironmentFile= path is not absolute',diagnostics)
            self.assertNotIn('Failed to resolve unit specifiers',diagnostics)

if __name__=='__main__':unittest.main(verbosity=2)
