"""Configure the published OpenAny CLI relay; no platform or CLI business-code changes."""
import hashlib
import json
from pathlib import Path
import re

import common
import setup_user
from common import Failure

PACKAGES = {'prod': ('openany-cli', '0.3.12', 'openany', 'https://api.openany.si'),
            'dev': ('openany-cli-dev', '0.1.5', 'openany-dev', 'https://api-dev.openany.si')}


def validate_node(node_version):
    match = re.fullmatch(r'v?(\d+)\.\d+\.\d+\s*', node_version)
    if not match or int(match[1]) < 22: raise Failure('Node.js 22+ is required for the current OpenAny CLI; install it before rerunning setup.')


def validate_help(environment, text, node_version):
    if environment not in PACKAGES: raise Failure('Select prod or dev.')
    validate_node(node_version)
    origin = PACKAGES[environment][3]
    if origin not in text or 'seller hermes serve' not in text or 'seller tasks upload' not in text:
        raise Failure('The selected CLI is outdated or belongs to another environment. Use the published ' + PACKAGES[environment][0] + ' package.')


def check_capabilities(caps):
    idem = caps.get('features', {}).get('runs_idempotency', {})
    retention = idem.get('retention_seconds')
    if idem.get('supported') is not True or idem.get('durable') is not True or not isinstance(retention, (int, float)) or isinstance(retention, bool) or retention < 30:
        raise Failure('Automatic CLI delivery requires Hermes durable Runs idempotency. Upgrade Hermes separately; this installer never changes Hermes itself.')
    return retention


def quote(value):
    value = str(value)
    if any(ord(c) < 32 for c in value): raise Failure('Control characters are not supported in service paths.')
    return json.dumps(value.replace('%', '%%'), ensure_ascii=False)


def command_quote(value):
    return quote(str(value).replace('$', '$$'))


def configure(home, environment, native_port, relay_port, node_bin, cli_bin, unit_root=None):
    home = Path(home)
    private = common.private_directory(home / '.openany-provider')
    marker = common.regular_file(private / 'delivery-environment')
    if marker.exists() and marker.read_text().strip() != environment:
        raise Failure('This Hermes home already has delivery configured for another environment. Reconcile its tasks or use an isolated Hermes home; no automatic environment migration was attempted.')
    existing = setup_user.read_environment(private / (environment + '.env'))
    variable = setup_user.seller_variable(environment)
    key = setup_user.validate_seller(existing.get(variable, ''))
    root = common.private_directory(Path(existing['OPENANY_TASK_ROOT']))
    state = common.private_directory(common.private_directory(private / 'delivery-state') / environment)
    native_port, relay_port = common.port_number(native_port), common.port_number(relay_port)
    if native_port == relay_port or relay_port in (80, 443): raise Failure('CLI and Hermes must use distinct loopback ports.')
    env = {variable: key, 'OPENANY_TASK_ROOT': str(root), 'OPENANY_HERMES_STATE_DIR': str(state),
           'OPENANY_HERMES_URL': 'http://127.0.0.1:' + str(native_port),
           'OPENANY_HERMES_API_KEY': common.read_key(home / 'openany-api.key'), 'OPENANY_HERMES_PORT': str(relay_port)}
    body = ''.join(name + '=' + json.dumps(value, ensure_ascii=False) + '\n' for name, value in env.items()).encode()
    env_file = common.regular_file(private / ('cli-' + environment + '.env'))
    unit = 'openany-hermes-delivery-' + environment + '-' + hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:12] + '.service'
    directory = Path(unit_root) if unit_root is not None else Path.home() / '.config/systemd/user'
    if directory.is_symlink(): raise Failure('Refusing a symlinked user service directory.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    service_file = common.regular_file(directory / unit)
    service = ('# Managed by OpenAny Hermes CLI setup v1\n[Unit]\nDescription=OpenAny Hermes CLI automatic file delivery\nAfter=network.target\n\n[Service]\nType=simple\n'
               + 'EnvironmentFile=' + quote(env_file) + '\n'
               + 'Environment=' + quote('PATH=' + str(Path(node_bin).parent) + ':/usr/local/bin:/usr/bin:/bin') + '\n'
               + 'ExecStart=' + command_quote(node_bin) + ' ' + command_quote(cli_bin) + ' seller hermes serve -j\n'
               + 'Restart=on-failure\nRestartSec=5\nUMask=0077\nTimeoutStopSec=45\n\n[Install]\nWantedBy=default.target\n').encode()
    if service_file.exists() and not service_file.read_text().startswith('# Managed by OpenAny Hermes CLI setup v1'):
        raise Failure('An unmanaged CLI delivery service already exists.')
    changed = (not env_file.exists() or env_file.read_bytes() != body or env_file.stat().st_mode & 0o077 or
               not service_file.exists() or service_file.read_bytes() != service)
    with common.Transaction(private / 'backups') as tx:
        if changed:
            tx.write(env_file, body)
            tx.write(service_file, service, 0o644)
        if not marker.exists(): tx.write(marker, (environment + '\n').encode())
    return {'changed': bool(changed), 'environment_file': str(env_file), 'unit': unit, 'unit_file': str(service_file),
            'relay_port': relay_port, 'task_root': str(root), 'state_directory': str(state), 'backup': str(tx.directory) if tx.directory else None,
            'seller_key_remote_verified': False}


def listing(domain, environment, skills, files_required=False):
    domain = common.domain_name(domain)
    entries = []
    for skill in skills:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}', skill) or '..' in skill.split('/'):
            raise Failure('Use a valid installed Skill identifier.')
        contract = {'format': 'json'}
        if files_required: contract.update(artifacts_required=True, artifact_delivery='auto_cli')
        entries.append({'skill': skill, 'fixed_body': {'instructions': 'Use skill_view to load the installed Skill ' + skill + ' and execute it for this request. Follow the platform delivery instructions.'},
                        'async_config': {'adapter_type': 'http_json', 'delivery_mode': 'poll', 'timeout_secs': 600,
                                         'output_contract': contract,
                                         'poll': {'interval_secs': 2}}})
    return {'base_url': 'https://' + domain, 'upstream_path': '/openany/v1/runs', 'method': 'POST',
            'content_type': 'application/json', 'authentication': 'Bearer: use the private Hermes API key',
            'upload_origin': PACKAGES[environment][3], 'upload_path': '/v1/agent-api-tasks/{task_id}/artifacts',
            'skills': entries, 'note': 'Configuration reference, not a submission/import document. Each product keeps its own price. Only file products require artifacts_required=true and artifact_delivery=auto_cli. Never configure /result for these poll tasks.'}
