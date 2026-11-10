"""JSON persistence for the Pydantic inventory, legacy migration, and the encrypted credential vault."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from project_models import BYTES_PER_MB, PROJECT_TYPES, ProjectManager

DATA = Path(os.getenv('LOCALAPPDATA') or Path.home() / '.project_hub') / 'ProjectHub' / 'projects.json'
RECENT = DATA.with_name('recent.json')
MAP_LAYOUT = DATA.with_name('map_layout.json')
SETTINGS = DATA.with_name('settings.json')
LOOSE_ITEMS = DATA.with_name('loose_items.json')
VAULT = DATA.with_name('credentials.dpapi')


class StorageError(Exception):
    pass


class VaultError(Exception):
    pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        tmp.write_text(text, encoding='utf8')
        tmp.replace(path)
    except OSError:
        if tmp.exists():
            tmp.unlink()
        raise


class ProjectStore:
    """Loads and saves a ProjectManager with model_validate_json() / model_dump_json()."""

    def __init__(self, path: Path = DATA, vault: CredentialVault | None = None):
        self.path = path
        self.vault = vault or CredentialVault()

    def load(self) -> ProjectManager:
        if not self.path.exists():
            return ProjectManager()
        try:
            text = self.path.read_text(encoding='utf8')
        except OSError as exc:
            raise StorageError(f'Could not read {self.path}:\n{exc}') from exc
        try:
            if text.lstrip().startswith('['):
                return self._migrate_legacy(text)
            return ProjectManager.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            backup = self._quarantine('unreadable')
            raise StorageError(f'Could not read {self.path}:\n{exc}\n\nThe file was copied to {backup} before starting with an empty inventory.') from exc

    def save(self, manager: ProjectManager) -> None:
        """Write the inventory. Passwords go to the vault, never into the JSON file.

        The JSON is written even if the vault fails, so other edits are not lost; the VaultError
        is re-raised afterwards and unsaved passwords stay in memory for the next attempt.
        """
        vault_error = None
        try:
            self._stash_passwords(manager)
        except (OSError, VaultError) as exc:
            vault_error = exc
        atomic_write(self.path, manager.model_dump_json(indent=2))
        if vault_error:
            raise VaultError(f'Project saved, but passwords could not be stored in the encrypted vault: {vault_error}')

    def export(self, manager: ProjectManager, target: Path) -> None:
        Path(target).write_text(manager.model_dump_json(indent=2), encoding='utf8')

    def _stash_passwords(self, manager: ProjectManager) -> None:
        pending = []
        for p in manager.projects:
            for label, creds in p.credential_slots():
                if creds.password and creds.password.get_secret_value():
                    ref = creds.credential_ref or f'{p.name} / {label} ({p.id[:6]})'
                    pending.append((creds, ref))
        if not pending:
            return
        secrets = self.vault.load()
        for creds, ref in pending:
            secrets[ref] = creds.password.get_secret_value()
        self.vault.save(secrets)
        for creds, ref in pending:
            creds.credential_ref = ref
            creds.password = None

    def _quarantine(self, reason: str) -> Path:
        backup = self.path.with_name(f'{self.path.stem}.{reason}-{time.strftime("%Y%m%d-%H%M%S")}{self.path.suffix}')
        shutil.copy2(self.path, backup)
        return backup

    def _migrate_legacy(self, text: str) -> ProjectManager:
        manager = ProjectManager.model_validate({'projects': [migrate_legacy_project(d) for d in json.loads(text)]})
        self._quarantine('v1-backup')
        atomic_write(self.path, manager.model_dump_json(indent=2))
        return manager


def migrate_legacy_project(d: dict) -> dict:
    """Convert a v1 flat Project dataclass dict into the nested v2 shape."""
    g = lambda key, default='': d.get(key) or default

    notes = g('notes')
    kind = g('kind').strip()
    is_website = d.get('is_website', bool(g('site') or g('domain')))
    if is_website:
        project_type = 'website'
    elif kind.lower() in PROJECT_TYPES:
        project_type = kind.lower()
    elif 'api' in kind.lower().split():
        project_type = 'api'
    else:
        project_type = 'app'
    if kind and kind.lower() != project_type:
        notes = f'{notes}\n\nOriginal project type: {kind}'.strip()

    scanned_at = g('scanned_at')
    created = date.today().isoformat()
    if scanned_at:
        try:
            created = datetime.fromisoformat(scanned_at).date().isoformat()
        except ValueError:
            pass

    host = g('ssh_host') or g('server_host')
    user = g('ssh_user') or g('server_user')
    credentials = None
    if host or user or g('server_credential_ref'):
        port = g('ssh_port') if g('ssh_host') else g('server_port') or g('ssh_port')
        credentials = {'username': user, 'host': host, 'port': port if str(port).isdigit() else None,
                       'credential_ref': g('server_credential_ref')}
    hosted = bool(host or g('deployment_platform') or g('server_path') or g('ssh_remote_path'))

    def reminder(prefix):
        days = g(f'{prefix}_remind_days', '30')
        return {'enabled': bool(d.get(f'{prefix}_remind')), 'days_before': int(days) if str(days).isdigit() and int(days) > 0 else 30}

    return {
        'id': g('id'),
        'name': g('name', 'Untitled project'),
        'description': g('description'),
        'source_location': g('path'),
        'machine': g('machine'),
        'status': g('status', 'Unknown'),
        'notes': notes,
        'date_created': created,
        'project_type': project_type,
        'size_mb': (d.get('size_bytes') or 0) / BYTES_PER_MB,
        'database': None,
        'deployment': {
            'environment': 'hosted' if hosted else 'local',
            'provider_name': g('hosting_provider') or g('deployment_platform'),
            'credentials': credentials,
            'stage': g('environment'),
            'site_url': g('site'),
            'health_url': g('health_url'),
            'platform': g('deployment_platform'),
            'runtime': g('site_runtime'),
            'service': g('service'),
            'remote_path': g('ssh_remote_path') or g('server_path'),
            'ssh_key_path': g('ssh_key_path'),
        },
        'website': {
            'domain': {'name': g('domain'), 'registrar': g('domain_registrar'), 'registrar_url': g('domain_registrar_url'),
                       'registered_on': g('domain_registered_on'), 'expires_on': g('domain_expires_on'),
                       'cost': g('domain_cost'), 'auto_renew': bool(d.get('domain_auto_renew')),
                       'reminder': reminder('domain')},
            'hosting': {'provider': g('hosting_provider'), 'provider_url': g('hosting_url'), 'plan': g('hosting_plan'),
                        'ip_address': g('hosting_ip'), 'purchased_on': g('hosting_purchased_on'),
                        'expires_on': g('hosting_expires_on'), 'cost': g('hosting_cost'),
                        'auto_renew': bool(d.get('hosting_auto_renew')), 'reminder': reminder('hosting')},
        },
        'cloud': {'provider': g('cloud_provider'), 'account': g('cloud_account'), 'project': g('cloud_project'),
                  'region': g('cloud_region'), 'service': g('cloud_service'), 'credential_ref': g('cloud_credential_ref')},
        'payments': {'provider': g('payment_provider'), 'mode': g('payment_mode'), 'account': g('payment_account'),
                     'webhook_url': g('payment_webhook_url'), 'credential_ref': g('payment_credential_ref')},
        'ai': {'provider': g('ai_provider'), 'model': g('ai_model'), 'endpoint': g('ai_endpoint'),
               'credential_ref': g('ai_credential_ref')},
        'paths': {'config': g('config_path'), 'data': g('data_path'), 'logs': g('log_path'), 'artifacts': g('artifact_path')},
        'commands': {'start': g('start_cmd'), 'restart': g('restart_cmd'), 'stop': g('stop_cmd')},
        'scan': {'scanned_at': scanned_at or None, 'file_count': d.get('file_count') or 0,
                 'modules': d.get('modules') or [], 'clues': d.get('clues') or []},
    }


def load_recent():
    if not RECENT.exists():
        return []
    try:
        return json.loads(RECENT.read_text(encoding='utf8'))
    except Exception:
        return []


def save_recent(ids):
    atomic_write(RECENT, json.dumps(ids))


def load_map_layout():
    """Project id -> [x, y] of each project's folder on the map view."""
    if not MAP_LAYOUT.exists():
        return {}
    try:
        return json.loads(MAP_LAYOUT.read_text(encoding='utf8'))
    except Exception:
        return {}


def save_map_layout(positions):
    atomic_write(MAP_LAYOUT, json.dumps(positions))


def load_loose_items():
    """Files/folders dropped on empty map space: [{'id', 'path', 'x', 'y'}]."""
    if not LOOSE_ITEMS.exists():
        return []
    try:
        data = json.loads(LOOSE_ITEMS.read_text(encoding='utf8'))
    except Exception:
        return []
    return [i for i in data if isinstance(i, dict) and i.get('path')] if isinstance(data, list) else []


def save_loose_items(items):
    atomic_write(LOOSE_ITEMS, json.dumps(items))


def load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        data = json.loads(SETTINGS.read_text(encoding='utf8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(settings):
    atomic_write(SETTINGS, json.dumps(settings, indent=2))


def find_editor(configured=''):
    """Executable (or command) to open files with: the configured editor, else VS Code, else Notepad."""
    configured = (configured or '').strip().strip('"')
    if configured:
        found = shutil.which(configured)
        if found or Path(configured).exists():
            return found or configured
    for name in ('code', 'code.cmd'):
        found = shutil.which(name)
        if found:
            return found
    local = os.getenv('LOCALAPPDATA')
    if local:
        candidate = Path(local) / 'Programs' / 'Microsoft VS Code' / 'bin' / 'code.cmd'
        if candidate.exists():
            return str(candidate)
    return 'notepad'


def open_in_editor(path, configured=''):
    """Open a file or folder in the editor. Returns the editor used. Raises OSError if nothing could be started."""
    editor = find_editor(configured)
    if Path(editor).stem.lower() == 'notepad' and Path(path).is_dir():
        # Notepad cannot open folders, so show the folder in Explorer instead.
        os.startfile(path)
        return 'explorer'
    subprocess.Popen([editor, str(path)], shell=editor.lower().endswith(('.cmd', '.bat')),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return editor


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
        plaintext = json.dumps(credentials, ensure_ascii=False).encode('utf8')
        envelope = {'version': 1, 'ciphertext': base64.b64encode(dpapi(plaintext)).decode('ascii')}
        atomic_write(self.path, json.dumps(envelope))
