"""Folder scanning, remote access, and background network/scanning workers."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import ssl
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from project_inference import infer_project

IGNORE = {'.git','node_modules','.venv','venv','__pycache__','.next','dist','build','.cache','target','.idea','.vscode','coverage'}
MAX_FILES = 300000

def scan(path):
    root = Path(path)
    if not root.is_dir():
        raise ValueError('Folder is not available on this computer')
    size = count = 0
    incomplete = False
    for folder, dirs, filenames in os.walk(root, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d not in IGNORE]
        for name in filenames:
            count += 1
            if count > MAX_FILES:
                incomplete = True
                break
            try:
                size += (Path(folder)/name).stat().st_size
            except OSError:
                pass
        if incomplete:
            break
    modules = set()
    clues = []
    def read(rel, limit=200000):
        try:
            return (root/rel).read_text(encoding='utf8', errors='replace')[:limit]
        except OSError:
            return ''
    requirements = read('requirements.txt')
    if requirements:
        clues.append('Python requirements.txt')
        for line in requirements.splitlines():
            line = line.strip()
            if line and not line.startswith(('#','-','.')):
                item = re.split(r'[<>=!~;\[\s]', line, 1)[0]
                if item:
                    modules.add(item)
    pyproject = read('pyproject.toml')
    if pyproject:
        clues.append('Python pyproject.toml')
        try:
            import tomllib
            data = tomllib.loads(pyproject)
            for dependency in data.get('project',{}).get('dependencies',[]):
                modules.add(re.split(r'[<>=!~;\[\s]', dependency, 1)[0])
            for depgroup in data.get('project',{}).get('optional-dependencies',{}).values():
                for dependency in depgroup:
                    modules.add(re.split(r'[<>=!~;\[\s]', dependency, 1)[0])
        except Exception:
            pass
    package = read('package.json')
    if package:
        clues.append('Node package.json')
        try:
            info = json.loads(package)
            modules.update(info.get('dependencies',{}))
            modules.update(info.get('devDependencies',{}))
            scripts = info.get('scripts',{})
            if 'start' in scripts:
                clues.append('npm start available')
            if 'dev' in scripts:
                clues.append('npm run dev available')
        except Exception:
            pass
    for filename, label in [('Dockerfile','Dockerfile'),('docker-compose.yml','Docker Compose'),('compose.yml','Docker Compose'),('compose.yaml','Docker Compose'),('nginx.conf','nginx config'),('manage.py','Django'),('app.py','Python app.py'),('main.py','Python main.py'),('Cargo.toml','Rust'),('go.mod','Go'),('Procfile','Procfile'),('.env','Environment file (do not share)')]:
        if (root/filename).is_file():
            clues.append(label)
    if (root/'.git').is_dir():
        clues.append('Git repository')
    if incomplete:
        clues.append('Partial scan: file limit reached')
    return size, count, sorted(modules, key=str.lower), clues

def remote_path(p):
    return (p.deployment.remote_path if p.deployment else '') or ''

def ssh_target(p):
    return p.remote.target if p.remote else ''

def ssh_key(p):
    return p.deployment.ssh_key_path if p.deployment else ''

def ssh_base_args(p):
    args = ['ssh', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'BatchMode=yes']
    port = p.remote.port if p.remote else None
    if port and port != 22:
        args += ['-p', str(port)]
    key = ssh_key(p)
    if key:
        args += ['-i', key]
    return args

def ssh_list(p):
    rp = remote_path(p) or '.'
    args = ssh_base_args(p) + [ssh_target(p), f'ls -la {shlex.quote(rp)}']
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return r.stdout, r.stderr, r.returncode
    except Exception as exc:
        return '', str(exc), 1

def ssh_exec(p, command):
    rp = remote_path(p) or '.'
    full = f'cd {shlex.quote(rp)} && {command}'
    args = ssh_base_args(p) + [ssh_target(p), full]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return r.stdout, r.stderr, r.returncode
    except Exception as exc:
        return '', str(exc), 1

def scp_download(p, remote_file, local_file):
    host = ssh_target(p)
    port = p.remote.port if p.remote else None
    rp = remote_path(p)
    remote = remote_file if (remote_file.startswith('/') or ':' in remote_file) else ((rp.rstrip('/') + '/' + remote_file) if rp else remote_file)
    args = ['scp', '-o', 'StrictHostKeyChecking=accept-new']
    if port and port != 22:
        args += ['-P', str(port)]
    key = ssh_key(p)
    if key:
        args += ['-i', key]
    args += [f'{host}:{remote}', str(local_file)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        return r.stdout, r.stderr, r.returncode
    except Exception as exc:
        return '', str(exc), 1

class SiteCheckWorker(QThread):
    finished = Signal(str, int, str)
    def __init__(self, url):
        super().__init__()
        self.url = url
    def run(self):
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(self.url, method='HEAD')
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                self.finished.emit(self.url, resp.status, '')
        except urllib.error.HTTPError as e:
            self.finished.emit(self.url, e.code, '')
        except Exception as exc:
            try:
                req = urllib.request.Request(self.url, method='GET')
                with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                    self.finished.emit(self.url, resp.status, '')
            except urllib.error.HTTPError as e:
                self.finished.emit(self.url, e.code, '')
            except Exception as exc2:
                self.finished.emit(self.url, 0, str(exc2))

def seo_audit(url):
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={'User-Agent': 'ProjectHub-SEO/1.0'})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            html = resp.read().decode('utf8', errors='replace')
            status = resp.status
    except Exception as exc:
        return f'Fetch failed: {exc}'
    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    title_text = title.group(1).strip() if title else ''
    meta_desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)
    if not meta_desc:
        meta_desc = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html, re.I)
    desc_text = meta_desc.group(1).strip() if meta_desc else ''
    h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.S)
    imgs = re.findall(r'<img[^>]*>', html, re.I)
    imgs_no_alt = [i for i in imgs if 'alt=' not in i.lower()]
    links = re.findall(r'<a[^>]+href=["\'](.*?)["\']', html, re.I)
    lines = []
    lines.append(f'HTTP status: {status}')
    lines.append(f'Title: {title_text if title_text else "(missing)"}  ({len(title_text)} chars)')
    lines.append(f'Meta description: {desc_text if desc_text else "(missing)"}  ({len(desc_text)} chars)')
    lines.append(f'H1 count: {len(h1s)}')
    if h1s:
        for h in h1s[:5]:
            lines.append(f'  - {re.sub("<.*?>", "", h).strip()[:80]}')
    lines.append(f'Images: {len(imgs)} total, {len(imgs_no_alt)} missing alt')
    lines.append(f'Links: {len(links)}')
    recs = []
    if not title_text:
        recs.append('Add a <title> tag.')
    elif len(title_text) < 30 or len(title_text) > 65:
        recs.append(f'Title length {len(title_text)} (aim 30-65).')
    if not desc_text:
        recs.append('Add a meta description.')
    elif len(desc_text) < 70 or len(desc_text) > 160:
        recs.append(f'Meta description length {len(desc_text)} (aim 70-160).')
    if len(h1s) != 1:
        recs.append(f'Expected exactly one H1, found {len(h1s)}.')
    if imgs_no_alt:
        recs.append(f'{len(imgs_no_alt)} images missing alt text.')
    if recs:
        lines.append('')
        lines.append('Recommendations:')
        for r in recs:
            lines.append('  - ' + r)
    else:
        lines.append('')
        lines.append('No obvious on-page SEO issues detected.')
    return '\n'.join(lines)

class ScanWorker(QThread):
    finished = Signal(str, str, object, str)
    def __init__(self, project_id, path, initial_data=None, infer_type=True, protected=None):
        super().__init__()
        self.project_id = project_id
        self.path = path
        self.initial_data = initial_data
        self.infer_type = infer_type
        self.protected = protected
    def run(self):
        try:
            inventory = scan(self.path)
            inference = infer_project(self.path, IGNORE) if self.initial_data is not None else None
            result = (inventory, inference, self.initial_data, self.infer_type, self.protected)
            error = None
        except Exception as exc:
            result = None
            error = str(exc)
        self.finished.emit(self.project_id, self.path, result, error)


def detect_public_ip(timeout=5):
    """Detect current public IP address via public echo services."""
    ctx = ssl.create_default_context()
    for url in ['https://api.ipify.org', 'https://checkip.amazonaws.com', 'https://ifconfig.me/ip']:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ProjectHub/1.0'})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                ip = resp.read().decode('utf8').strip()
                if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                    return ip
        except Exception:
            continue
    return ''

def hostinger_request(api_token, method, path, body=None, timeout=15):
    """Call the Hostinger API and return decoded JSON (None for an empty body), raising readable errors."""
    if not api_token or not api_token.strip():
        raise ValueError('Hostinger API token is required.')
    headers = {
        'Authorization': f'Bearer {api_token.strip()}',
        'Accept': 'application/json',
        'User-Agent': 'ProjectHub/1.0',
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'https://developers.hostinger.com{path}', data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            text = resp.read().decode('utf8')
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf8', errors='replace')
        try:
            err_json = json.loads(err_body)
            msg = err_json.get('message') or err_json.get('error') or err_body
            if isinstance(err_json.get('errors'), dict):
                msg += ' ' + '; '.join(f'{k}: {v}' for k, v in err_json['errors'].items())
        except Exception:
            msg = err_body[:200]
        raise RuntimeError(f'Hostinger API error ({exc.code}): {msg}') from exc
    except Exception as exc:
        raise RuntimeError(f'Could not reach Hostinger API: {exc}') from exc
    return json.loads(text) if text.strip() else None

def fetch_hostinger_domains(api_token, timeout=12):
    """Fetch domains registered under a Hostinger account."""
    if not api_token or not api_token.strip():
        raise ValueError('Hostinger API token is required.')
    url = 'https://developers.hostinger.com/api/domains/v1/portfolio'
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {api_token.strip()}',
        'Accept': 'application/json',
        'User-Agent': 'ProjectHub/1.0',
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf8'))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf8', errors='replace')
        try:
            err_json = json.loads(err_body)
            msg = err_json.get('message') or err_json.get('error') or err_body
        except Exception:
            msg = err_body[:200]
        raise RuntimeError(f'Hostinger API error ({exc.code}): {msg}') from exc
    except Exception as exc:
        raise RuntimeError(f'Could not reach Hostinger API: {exc}') from exc

    domains = data.get('data', [])
    results = []
    for item in domains:
        exp = item.get('expires_at') or ''
        results.append({
            'domain': item.get('domain', ''),
            'status': item.get('status', 'active'),
            'expires_on': exp[:10] if len(exp) >= 10 else exp,
            'auto_renew': bool(item.get('auto_renewal', False)),
            'provider': 'Hostinger',
            'provider_url': 'https://hpanel.hostinger.com',
            'raw': item,
        })
    return results

def fetch_hostinger_vps(api_token, timeout=12):
    """Fetch VPS / virtual machines under a Hostinger account."""
    if not api_token or not api_token.strip():
        raise ValueError('Hostinger API token is required.')
    url = 'https://developers.hostinger.com/api/vps/v1/virtual-machines'
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {api_token.strip()}',
        'Accept': 'application/json',
        'User-Agent': 'ProjectHub/1.0',
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf8'))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf8', errors='replace')
        try:
            err_json = json.loads(err_body)
            msg = err_json.get('message') or err_json.get('error') or err_body
        except Exception:
            msg = err_body[:200]
        raise RuntimeError(f'Hostinger API error ({exc.code}): {msg}') from exc
    except Exception as exc:
        raise RuntimeError(f'Could not reach Hostinger API: {exc}') from exc

    vms = data.get('data', [])
    results = []
    for item in vms:
        cpu = item.get('cpu')
        ram = item.get('ram')
        plan = f"VPS ({cpu} vCPU, {ram // 1024 if isinstance(ram, int) else ram}GB RAM)" if cpu else 'VPS'
        results.append({
            'id': str(item.get('id', '')),
            'name': item.get('name', ''),
            'status': item.get('status', ''),
            'ip_address': item.get('ip_address', ''),
            'os': item.get('os', ''),
            'plan': plan,
            'provider': 'Hostinger',
            'provider_url': 'https://hpanel.hostinger.com',
            'raw': item,
        })
    return results

def namecheap_request(username, api_key, client_ip, command, extra=None, sandbox=False, post=False, timeout=15):
    """Call a Namecheap XML API command and return the parsed root element, raising on API errors."""
    if not username or not username.strip():
        raise ValueError('Namecheap API username is required.')
    if not api_key or not api_key.strip():
        raise ValueError('Namecheap API key is required.')
    if not client_ip or not client_ip.strip():
        raise ValueError('Whitelisted Client IP is required for Namecheap API.')

    base_url = 'https://api.sandbox.namecheap.com/xml.response' if sandbox else 'https://api.namecheap.com/xml.response'
    params = urllib.parse.urlencode({
        'ApiUser': username.strip(),
        'ApiKey': api_key.strip(),
        'UserName': username.strip(),
        'Command': command,
        'ClientIp': client_ip.strip(),
        **(extra or {}),
    })
    headers = {'User-Agent': 'ProjectHub/1.0'}
    if post:
        # setHosts can carry many records; Namecheap recommends POST beyond 10 hosts.
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(base_url, data=params.encode('utf8'), headers=headers, method='POST')
    else:
        req = urllib.request.Request(f'{base_url}?{params}', headers=headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            xml_text = resp.read().decode('utf8', errors='replace')
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf8', errors='replace')
        raise RuntimeError(f'Namecheap API HTTP error ({exc.code}): {err_body[:200]}') from exc
    except Exception as exc:
        raise RuntimeError(f'Could not reach Namecheap API: {exc}') from exc

    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        raise RuntimeError(f'Invalid XML received from Namecheap: {exc}') from exc

    status = root.attrib.get('Status', '')
    errors = []
    for el in root.iter():
        tag = el.tag.split('}')[-1]
        if tag == 'Error' and el.text:
            errors.append(el.text.strip())

    if status != 'OK' or errors:
        msg = '; '.join(errors) or 'Unknown Namecheap error'
        if 'not whitelisted' in msg.lower():
            msg += f'\n\nTip: Add your current public IP ({client_ip}) to Whitelisted IPs under Namecheap -> Profile -> Tools -> API Access.'
        raise RuntimeError(f'Namecheap API error: {msg}')
    return root

def fetch_namecheap_domains(username, api_key, client_ip, sandbox=False, timeout=15):
    """Fetch domains registered under a Namecheap account using their XML API."""
    root = namecheap_request(username, api_key, client_ip, 'namecheap.domains.getList', {'PageSize': '100'},
                             sandbox=sandbox, timeout=timeout)

    def convert_nc_date(d_str):
        if not d_str:
            return ''
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', d_str)
        if m:
            month, day, year = m.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return d_str

    results = []
    for el in root.iter():
        tag = el.tag.split('}')[-1]
        if tag == 'Domain':
            attrs = el.attrib
            name = attrs.get('Name', '')
            created = attrs.get('Created', '')
            expires = attrs.get('Expires', '')
            auto_renew = attrs.get('AutoRenew', '').lower() == 'true'
            is_expired = attrs.get('IsExpired', '').lower() == 'true'
            results.append({
                'domain': name,
                'status': 'Expired' if is_expired else 'Active',
                'registered_on': convert_nc_date(created),
                'expires_on': convert_nc_date(expires),
                'auto_renew': auto_renew,
                'provider': 'Namecheap',
                'provider_url': 'https://ap.www.namecheap.com',
                'raw': dict(attrs),
            })
    return results

class TaskWorker(QThread):
    """Run fn(*args) off the UI thread; emits (result, '') or (None, error message)."""
    finished = Signal(object, str)
    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args
    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as exc:
            self.finished.emit(None, str(exc) or exc.__class__.__name__)
            return
        self.finished.emit(result, '')

class AccountSyncWorker(QThread):
    finished = Signal(dict, dict)  # (data, errors)
    def __init__(self, hostinger_token=None, nc_user=None, nc_key=None, nc_ip=None):
        super().__init__()
        self.hostinger_token = hostinger_token
        self.nc_user = nc_user
        self.nc_key = nc_key
        self.nc_ip = nc_ip

    def run(self):
        data = {'hostinger_domains': [], 'hostinger_vps': [], 'namecheap_domains': []}
        errors = {}
        if self.hostinger_token:
            try:
                data['hostinger_domains'] = fetch_hostinger_domains(self.hostinger_token)
            except Exception as e:
                errors['hostinger_domains'] = str(e)
            try:
                data['hostinger_vps'] = fetch_hostinger_vps(self.hostinger_token)
            except Exception as e:
                errors['hostinger_vps'] = str(e)

        if self.nc_user and self.nc_key:
            client_ip = self.nc_ip or detect_public_ip()
            try:
                data['namecheap_domains'] = fetch_namecheap_domains(self.nc_user, self.nc_key, client_ip)
            except Exception as e:
                errors['namecheap'] = str(e)

        self.finished.emit(data, errors)
