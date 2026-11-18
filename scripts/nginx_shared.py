"""Conservative shared HTTPS-site integration; never replace dashboard or TLS settings."""
import fnmatch
import hashlib
from pathlib import Path
import re
import shlex
import time

import common
from common import Failure

BEGIN = '# BEGIN OpenAny CLI delivery v1'
END = '# END OpenAny CLI delivery v1'
PREFIX = '/openany'


def tokens(text):
    result, index = [], 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] == '#':
            end = text.find('\n', index)
            index = len(text) if end < 0 else end + 1
            continue
        start = index
        if text[index] in '{};':
            result.append((text[index], index, index + 1)); index += 1; continue
        quote = None
        while index < len(text):
            char = text[index]
            if char == '\\':
                index += 2; continue
            if quote:
                if char == quote: quote = None
                index += 1; continue
            if char in ('"', "'"):
                quote = char; index += 1; continue
            if text.startswith('${', index):
                end = text.find('}', index + 2)
                if end < 0: raise Failure('Unclosed Nginx variable.')
                index = end + 1; continue
            if char.isspace() or char in '{};#': break
            index += 1
        if quote or index == start: raise Failure('Unsupported Nginx syntax; review the existing site.')
        raw = text[start:index]
        # Quoted values are used only for analysis; byte offsets preserve the original file.
        if raw[:1] in ('"', "'") and raw[-1:] == raw[:1]:
            raw = raw[1:-1]
        result.append((raw, start, index))
    return result


def parse(text):
    stream = tokens(text)
    def group(index, nested=False):
        nodes, header = [], []
        while index < len(stream):
            value, start, end = stream[index]
            if value == '}':
                if not nested or header: raise Failure('Ambiguous Nginx braces.')
                return nodes, index + 1, start
            if value == '{':
                if not header: raise Failure('Missing Nginx block header.')
                children, index, closing = group(index + 1, True)
                nodes.append({'header': tuple(x[0] for x in header), 'children': children, 'start': header[0][1], 'end': closing})
                header = []; continue
            if value == ';':
                if not header: raise Failure('Empty Nginx directive.')
                nodes.append({'header': tuple(x[0] for x in header), 'children': None, 'start': header[0][1], 'end': end})
                header = []; index += 1; continue
            header.append(stream[index]); index += 1
        if nested or header: raise Failure('Incomplete Nginx configuration.')
        return nodes, index, len(text)
    return group(0)[0]


def read_sources(dump):
    paths = re.findall(r'^# configuration file (.+):$', dump, re.M)
    sources = {}
    for value in paths:
        path = Path(value)
        if not path.is_absolute(): raise Failure('Nginx configuration paths must be absolute.')
        path = path.resolve(strict=True)  # sites-enabled symlinks retain their actual target.
        sources[str(path)] = common.regular_file(path).read_text()
    if not sources: raise Failure('Nginx did not report any loaded configuration files.')
    return sources


def select_site(sources, domain=None, site_path=None):
    if domain: domain = common.domain_name(domain)
    parsed = {path: parse(text) for path, text in sources.items()}
    def expand(nodes, visited=()):
        expanded = []
        for node in nodes:
            header = node['header']
            if header[0] == 'include' and node['children'] is None:
                if len(header) != 2 or len(visited) > 20: raise Failure('Unsupported Nginx include.')
                pattern = header[1]
                if not pattern.startswith('/'): pattern = '/etc/nginx/' + pattern
                matched = [p for p in sources if fnmatch.fnmatchcase(p, pattern) or fnmatch.fnmatchcase(str(Path(p)), pattern)]
                # Canonical paths may differ from sites-enabled paths in the dump. Only includes
                # inside the selected server need expansion; standard SSL snippets are exact files.
                if not matched and not any(c in pattern for c in '*?['):
                    candidate = str(Path(pattern).resolve())
                    if candidate in parsed: matched = [candidate]
                if not matched: raise Failure('An included Nginx snippet cannot be reviewed automatically: ' + pattern)
                for path in matched:
                    if path in visited: raise Failure('Recursive Nginx include.')
                    expanded.extend(expand(parsed[path], (*visited, path)))
            else: expanded.append(node)
        return expanded
    def servers(nodes):
        for node in nodes:
            if node['children'] is not None:
                if node['header'] == ('server',): yield node
                elif node['header'] == ('http',): yield from servers(node['children'])
    candidates = []
    for path, nodes in parsed.items():
        if site_path and str(Path(path).resolve()) != str(Path(site_path).resolve()): continue
        for node in servers(nodes):
            direct = expand(node['children'])
            headers = [n['header'] for n in direct]
            if not any(h[0] == 'listen' and 'ssl' in h and any(re.search(r'(?:^|:)443$', x) for x in h[1:]) for h in headers): continue
            names = [name for h in headers if h[0] == 'server_name' for name in h[1:]]
            if domain and domain not in names: continue
            names = [n for n in names if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}', n)]
            if not domain and len(names) != 1: raise Failure('Provide --domain using an existing HTTPS hostname; automatic domain selection is ambiguous.')
            if not names: continue
            chosen = domain or common.domain_name(names[0])
            if not all(any(h[0] == key for h in headers) for key in ('ssl_certificate', 'ssl_certificate_key')):
                raise Failure('The existing HTTPS certificate configuration needs manual review.')
            if any(h[0] in ('return', 'rewrite', 'if') for h in headers):
                raise Failure('A server-level redirect/rewrite would intercept the fixed API path; review this existing site, not DNS.')
            text = sources[path]
            start, end = text.find(BEGIN), text.find(END)
            if (start < 0) != (end < 0) or text.count(BEGIN) > 1 or text.count(END) > 1 or (start >= 0 and end < start):
                raise Failure('Incomplete or duplicated managed Nginx route markers.')
            for child in direct:
                h = child['header']
                if h[0] != 'location': continue
                route = h[2] if len(h) > 2 and h[1] in ('=', '^~', '~', '~*') else h[1] if len(h) > 1 else ''
                if route == PREFIX or route.startswith(PREFIX + '/'):
                    # Only a block in this file, inside this selected server and managed span, may be replaced.
                    if child not in node['children'] or not (node['start'] < start <= child['start'] < end < node['end']):
                        raise Failure('An existing location owns /openany; automatic overwrite was refused.')
            if start >= 0 and not node['start'] < start < end < node['end']:
                raise Failure('Managed API routes belong to another server block.')
            auth_request = any(token[0] == 'auth_request' for source in sources.values() for token in tokens(source))
            candidates.append({'domain': chosen, 'path': path, 'text': text, 'end': node['end'], 'auth_request': auth_request, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
    if len(candidates) != 1:
        raise Failure('Select one existing HTTPS site with --domain and, if needed, --nginx-site. No new domain is required.')
    return candidates[0]


def render_location(port, auth_request=False):
    port = common.port_number(port)
    auth = '        auth_request off;\n' if auth_request else ''
    return f'''    {BEGIN}
    location ^~ /openany/ {{
        auth_basic off;
{auth}        proxy_pass http://127.0.0.1:{port}/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Idempotency-Key $http_idempotency_key;
        proxy_set_header X-OpenAny-Task-Id $http_x_openany_task_id;
        proxy_set_header X-OpenAny-Deadline $http_x_openany_deadline;
        proxy_set_header X-OpenAny-Artifact-Delivery $http_x_openany_artifact_delivery;
        proxy_buffering off;
        proxy_cache off;
        proxy_intercept_errors off;
        proxy_next_upstream off;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
        limit_except GET POST {{ deny all; }}
    }}
    {END}
'''


def patch_site(text, selected, port):
    block = render_location(port, selected.get('auth_request', False))
    if BEGIN in text:
        start = text.rfind('\n', 0, text.index(BEGIN)) + 1
        end = text.index(END) + len(END)
        if text[end:end + 1] == '\n': end += 1
        return text[:start] + block + text[end:]
    end = selected['end']
    return text[:end] + ('\n' if text[:end] and not text[:end].endswith('\n') else '') + block + text[end:]


def verify_after_reload(verifier):
    # A successful reload request precedes activation of Nginx's new workers.
    # Keep the original backup until the complete authenticated check succeeds.
    for attempt in range(10):
        try:
            return verifier()
        except Failure:
            if attempt == 9:
                raise
            time.sleep(1)


def apply_site(selected, port, backups, runner, verifier=None):
    path = common.regular_file(Path(selected['path']))
    original = path.read_text()
    if hashlib.sha256(original.encode()).hexdigest() != selected['sha256']:
        raise Failure('Existing Nginx site changed since inspection; rerun to review the latest configuration.')
    desired = patch_site(original, selected, port).encode()
    if desired == original.encode():
        runner(['nginx','-t'])
        if verifier: verify_after_reload(verifier)
        return {'changed': False, 'site': str(path), 'certificate': 'reused', 'backup': None}
    try:
        with common.Transaction(Path(backups)) as tx:
            tx.write(path, desired, path.stat().st_mode & 0o777)
            runner(['nginx','-t'])
            runner(['systemctl','reload','nginx'])
            if verifier: verify_after_reload(verifier)
        return {'changed': True, 'site': str(path), 'certificate': 'reused', 'backup': str(tx.directory)}
    except BaseException:
        try:
            runner(['nginx','-t']); runner(['systemctl','reload','nginx'])
        except Exception:
            raise Failure('Existing site was restored but Nginx reload failed; inspect the reported private backups.') from None
        raise


def verify_public(domain, key, fetch=common.request_json):
    origin = 'https://' + common.domain_name(domain)
    report = common.verify_api(origin, key, lambda base, path, token=None: fetch(base, PREFIX + path, token))
    return {**report, 'origin': origin, 'base_url': origin + PREFIX, 'runs_url': origin + PREFIX + '/v1/runs', 'skills_url': origin + PREFIX + '/v1/skills', 'capabilities_url': origin + PREFIX + '/v1/capabilities'}
