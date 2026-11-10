"""Project records, local persistence, scanning, and encrypted credentials."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

DATA = Path(os.getenv('LOCALAPPDATA') or Path.home() / '.project_hub') / 'ProjectHub' / 'projects.json'
RECENT = DATA.with_name('recent.json')
VAULT = DATA.with_name('credentials.dpapi')
IGNORE = {'.git','node_modules','.venv','venv','__pycache__','.next','dist','build','.cache','target','.idea','.vscode','coverage'}
MAX_FILES = 300000

class VaultError(Exception):
    pass

class DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise VaultError('Encrypted credential storage is supported on Windows only.')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    source = ctypes.create_string_buffer(data)
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    result_blob = DataBlob()
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    if decrypt:
        description = ctypes.c_wchar_p()
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        success = operation(ctypes.byref(source_blob), ctypes.byref(description), None, None, None, 1, ctypes.byref(result_blob))
        if description:
            local_free(ctypes.cast(description, ctypes.c_void_p))
    else:
        operation = crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        success = operation(ctypes.byref(source_blob), 'Project Hub credentials', None, None, None, 1, ctypes.byref(result_blob))
    if not success:
        raise OSError(ctypes.get_last_error(), 'Windows could not encrypt or decrypt the credential vault.')
    try:
        return ctypes.string_at(result_blob.pbData, result_blob.cbData)
    finally:
        local_free(ctypes.cast(result_blob.pbData, ctypes.c_void_p))

class CredentialVault:
    def __init__(self, path=VAULT):
        self.path = path
    def load(self):
        if not self.path.exists():
            return {}
        try:
            envelope = json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(envelope, dict):
                raise VaultError('Credential vault format is not recognized.')
            if envelope.get('version') != 1 or not isinstance(envelope.get('ciphertext'), str):
                raise VaultError('Credential vault format is not recognized.')
            plaintext = dpapi(base64.b64decode(envelope['ciphertext'], validate=True), decrypt=True)
            credentials = json.loads(plaintext.decode('utf8'))
            if not isinstance(credentials, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in credentials.items()):
                raise VaultError('Credential vault contents are invalid.')
            return credentials
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise VaultError(f'Could not decrypt or read the credential vault: {exc}') from exc
    def save(self, credentials):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(credentials, ensure_ascii=False).encode('utf8')
        envelope = {'version': 1, 'ciphertext': base64.b64encode(dpapi(plaintext)).decode('ascii')}
        tmp = self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(envelope), encoding='utf8')
            tmp.replace(self.path)
        except OSError:
            if tmp.exists():
                tmp.unlink()
            raise

@dataclass
class Project:
    id: str = ''
    name: str = ''
    description: str = ''
    path: str = ''
    machine: str = ''
    kind: str = ''
    status: str = 'Unknown'
    site: str = ''
    domain: str = ''
    server_host: str = ''
    server_port: str = ''
    server_user: str = ''
    server_credential_ref: str = ''
    server_path: str = ''
    deployment_platform: str = ''
    service: str = ''
    site_runtime: str = ''
    environment: str = ''
    start_cmd: str = ''
    restart_cmd: str = ''
    stop_cmd: str = ''
    health_url: str = ''
    cloud_provider: str = ''
    cloud_account: str = ''
    cloud_project: str = ''
    cloud_region: str = ''
    cloud_service: str = ''
    cloud_credential_ref: str = ''
    payment_provider: str = ''
    payment_mode: str = ''
    payment_account: str = ''
    payment_webhook_url: str = ''
    payment_credential_ref: str = ''
    ai_provider: str = ''
    ai_model: str = ''
    ai_endpoint: str = ''
    ai_credential_ref: str = ''
    config_path: str = ''
    data_path: str = ''
    log_path: str = ''
    artifact_path: str = ''
    notes: str = ''
    size_bytes: int = 0
    file_count: int = 0
    modules: list = field(default_factory=list)
    clues: list = field(default_factory=list)
    scanned_at: str = ''
    ssh_host: str = ''
    ssh_port: str = '22'
    ssh_user: str = ''
    ssh_key_path: str = ''
    ssh_remote_path: str = ''
    is_website: bool = False
    domain_registrar: str = ''
    domain_registrar_url: str = ''
    domain_registered_on: str = ''
    domain_expires_on: str = ''
    domain_cost: str = ''
    domain_auto_renew: bool = False
    domain_remind: bool = False
    domain_remind_days: str = '30'
    hosting_provider: str = ''
    hosting_url: str = ''
    hosting_plan: str = ''
    hosting_ip: str = ''
    hosting_purchased_on: str = ''
    hosting_expires_on: str = ''
    hosting_cost: str = ''
    hosting_auto_renew: bool = False
    hosting_remind: bool = False
    hosting_remind_days: str = '30'

BOOL_FIELDS = {'is_website', 'domain_auto_renew', 'domain_remind', 'hosting_auto_renew', 'hosting_remind'}
DATE_FIELDS = {'domain_registered_on', 'domain_expires_on', 'hosting_purchased_on', 'hosting_expires_on'}
DAY_FIELDS = {'domain_remind_days', 'hosting_remind_days'}
RENEWALS = [
    ('Domain', 'domain_expires_on', 'domain_remind', 'domain_remind_days'),
    ('Hosting', 'hosting_expires_on', 'hosting_remind', 'hosting_remind_days'),
]

def parse_date(text):
    try:
        return date.fromisoformat((text or '').strip())
    except ValueError:
        return None

def upcoming_renewals(projects, today=None):
    """Return (days_left, project, label, due_date) for website renewals whose reminder window has opened."""
    today = today or date.today()
    due = []
    for p in projects:
        if not p.is_website:
            continue
        for label, expires_key, remind_key, days_key in RENEWALS:
            expires = parse_date(getattr(p, expires_key))
            if not expires or not getattr(p, remind_key):
                continue
            try:
                window = int(getattr(p, days_key) or 30)
            except ValueError:
                window = 30
            days_left = (expires - today).days
            if days_left <= window:
                due.append((days_left, p, label, expires))
    return sorted(due, key=lambda item: (item[0], item[1].name.lower()))

def renewal_text(days_left):
    if days_left < 0:
        return f'OVERDUE by {-days_left} day(s)'
    if days_left == 0:
        return 'due TODAY'
    return f'due in {days_left} day(s)'

def human(n):
    n = float(n or 0)
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024

def load():
    if not DATA.exists():
        return []
    try:
        raw = json.loads(DATA.read_text(encoding='utf8'))
        known = {f.name for f in fields(Project)}
        projects = []
        for d in raw:
            project = Project(**{k:v for k,v in d.items() if k in known})
            if 'is_website' not in d and (project.site or project.domain):
                project.is_website = True
            projects.append(project)
        return projects
    except Exception as exc:
        QMessageBox.critical(None, 'Database error', f'Could not read {DATA}:\n{exc}\nYour file was not modified.')
        return []

def save(projects):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix('.tmp')
    tmp.write_text(json.dumps([asdict(p) for p in projects], indent=2, ensure_ascii=False), encoding='utf8')
    tmp.replace(DATA)

def load_recent():
    if not RECENT.exists():
        return []
    try:
        return json.loads(RECENT.read_text(encoding='utf8'))
    except Exception:
        return []

def save_recent(ids):
    RECENT.parent.mkdir(parents=True, exist_ok=True)
    RECENT.write_text(json.dumps(ids), encoding='utf8')

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

FIELD_GROUPS = [
 ('Overview', [('name','Project name'),('description','What it does'),('path','Local folder'),('machine','Computer / location'),('kind','Project type'),('status','Status'),('notes','Notes')]),
 ('Website: domain & hosting', [
   ('is_website','This project is a website'),
   ('domain','Domain name'),('domain_registrar','Domain registrar / purchased from'),('domain_registrar_url','Registrar site URL'),
   ('domain_registered_on','Domain registered on (YYYY-MM-DD)'),('domain_expires_on','Domain renewal / expiry date (YYYY-MM-DD)'),
   ('domain_cost','Domain renewal cost'),('domain_auto_renew','Domain auto-renew is on'),
   ('domain_remind','Remind me before domain renewal'),('domain_remind_days','Domain reminder: days before'),
   ('hosting_provider','Hosting purchased from'),('hosting_url','Hosting provider site URL'),('hosting_plan','Hosting plan'),
   ('hosting_ip','Hosting IP address'),('hosting_purchased_on','Hosting purchased on (YYYY-MM-DD)'),
   ('hosting_expires_on','Hosting renewal / expiry date (YYYY-MM-DD)'),('hosting_cost','Hosting renewal cost'),
   ('hosting_auto_renew','Hosting auto-renew is on'),('hosting_remind','Remind me before hosting renewal'),
   ('hosting_remind_days','Hosting reminder: days before')]),
 ('Server & runtime', [('site','Website URL'),('health_url','Health-check URL'),('server_host','Server / SSH host'),('server_port','Server port'),('server_user','Server user'),('server_credential_ref','Credential vault key (optional)'),('server_path','Path on server'),('deployment_platform','Deployment platform'),('site_runtime','Runtime / web stack'),('service','Service / container name'),('environment','Environment (dev / staging / prod)')]),
 ('Cloud', [('cloud_provider','Cloud provider'),('cloud_account','Account / tenant'),('cloud_project','Cloud project / subscription'),('cloud_region','Region'),('cloud_service','Cloud service / resource'),('cloud_credential_ref','Credential vault key (optional)')]),
 ('Payments', [('payment_provider','Payment provider'),('payment_mode','Mode (test / live)'),('payment_account','Merchant / account ID'),('payment_webhook_url','Webhook URL'),('payment_credential_ref','Credential vault key (optional)')]),
 ('AI', [('ai_provider','AI provider'),('ai_model','Model'),('ai_endpoint','API endpoint'),('ai_credential_ref','Credential vault key (optional)')]),
 ('Files & paths', [('config_path','Configuration file / directory'),('data_path','Data location'),('log_path','Log location'),('artifact_path','Build / artifact location')]),
 ('Commands', [('start_cmd','Start command'),('restart_cmd','Restart command'),('stop_cmd','Stop command')]),
 ('SSH / Remote', [('ssh_host','SSH host'),('ssh_port','SSH port'),('ssh_user','SSH user'),('ssh_key_path','SSH private key file'),('ssh_remote_path','Remote project path')]),
]
