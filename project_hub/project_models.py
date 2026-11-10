"""Strongly typed, nested Pydantic (v2) models for the Project Hub inventory."""
from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime
from typing import Iterator, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

DatabaseProvider = Literal['mysql', 'postgres', 'sqlite', 'mongodb', 'mssql', 'none']
DeploymentEnvironment = Literal['local', 'hosted']
ProjectType = Literal['app', 'website', 'api']
DnsRecordType = Literal['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA']

DATABASE_PROVIDERS: tuple[str, ...] = DatabaseProvider.__args__
DEPLOYMENT_ENVIRONMENTS: tuple[str, ...] = DeploymentEnvironment.__args__
PROJECT_TYPES: tuple[str, ...] = ProjectType.__args__
DNS_RECORD_TYPES: tuple[str, ...] = DnsRecordType.__args__
DOMAIN_PROVIDERS = ('Namecheap', 'GoDaddy', 'Cloudflare', 'Hostinger', 'Porkbun', 'Squarespace Domains',
                    'Amazon Route 53', 'Google Cloud DNS', 'IONOS', 'Name.com', 'Gandi', 'Dynadot')
BYTES_PER_MB = 1024 * 1024


class Section(BaseModel):
    """Base for every sub-model.

    Unknown keys are ignored so older/newer files still load. After validation, any optional
    child section that holds only default values collapses to None, so "unused" really is None.
    """
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)

    @field_validator('*', mode='before')
    @classmethod
    def _blank_to_default(cls, value, info):
        # Form fields and legacy files use '' for "not set": give non-text fields their default
        # (None for optional dates/ports, 30 reminder days, today's date_created) instead of failing.
        field = cls.model_fields[info.field_name]
        if value != '' or field.is_required() or isinstance(field.default, str):
            return value
        return field.get_default(call_default_factory=True)

    @model_validator(mode='after')
    def _prune_blank_sections(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, Section) and value.is_blank():
                setattr(self, name, None)
        return self

    def is_blank(self) -> bool:
        return self == type(self)()


class SecretCredentials(Section):
    username: str = ''
    # Never written to projects.json; the storage layer moves it into the encrypted vault.
    password: Optional[SecretStr] = Field(default=None, exclude=True)
    host: str = ''
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    credential_ref: str = ''  # vault key holding the password

    @property
    def target(self) -> str:
        """user@host for SSH, or just the host."""
        return f'{self.username}@{self.host}' if self.username and self.host else self.host


class DatabaseConfig(Section):
    provider: DatabaseProvider = 'none'
    db_name: str = ''
    credentials: Optional[SecretCredentials] = None


class DeploymentConfig(Section):
    environment: DeploymentEnvironment = 'local'
    provider_name: str = ''
    credentials: Optional[SecretCredentials] = None
    stage: str = ''  # dev / staging / prod
    site_url: str = ''
    health_url: str = ''
    platform: str = ''
    runtime: str = ''
    service: str = ''
    remote_path: str = ''
    ssh_key_path: str = ''


class Reminder(Section):
    enabled: bool = False
    days_before: int = Field(default=30, ge=1)


class _Renewable(Section):
    expires_on: Optional[date] = None
    cost: str = ''
    auto_renew: bool = False
    reminder: Optional[Reminder] = None

    @model_validator(mode='after')
    def _reminder_needs_date(self):
        if self.reminder and self.reminder.enabled and not self.expires_on:
            raise ValueError('Enter a renewal / expiry date to enable its reminder.')
        return self


class DomainInfo(_Renewable):
    name: str = ''
    registrar: str = ''
    registrar_url: str = ''
    registered_on: Optional[date] = None


class HostingInfo(_Renewable):
    provider: str = ''
    provider_url: str = ''
    plan: str = ''
    ip_address: str = ''
    purchased_on: Optional[date] = None


_LABEL = r'[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?'
HOSTNAME_RE = re.compile(rf'^{_LABEL}(?:\.{_LABEL})*\.?$')
RECORD_NAME_RE = re.compile(rf'^(?:@|\*|(?:\*\.)?{_LABEL}(?:\.{_LABEL})*)$')


def _article(record_type):
    return 'An' if record_type in ('A', 'AAAA', 'MX', 'NS', 'SRV') else 'A'


class DnsRecord(Section):
    type: DnsRecordType = 'A'
    name: str = '@'  # host relative to the domain; '@' is the apex
    value: str = Field(min_length=1)
    ttl: int = Field(default=3600, ge=1, le=604800)
    priority: Optional[int] = Field(default=None, ge=0, le=65535)  # MX and SRV only
    notes: str = ''

    def is_blank(self) -> bool:
        return False

    @field_validator('name')
    @classmethod
    def _check_name(cls, value):
        value = value or '@'
        if not RECORD_NAME_RE.match(value):
            raise ValueError(f'"{value}" is not a valid record name. Use @ for the domain itself, or a host such as www.')
        return value

    @model_validator(mode='after')
    def _check_value(self):
        t, v = self.type, self.value
        if t == 'A':
            try:
                ipaddress.IPv4Address(v)
            except ValueError:
                raise ValueError('An A record needs an IPv4 address, for example 203.0.113.10.') from None
        elif t == 'AAAA':
            try:
                ipaddress.IPv6Address(v)
            except ValueError:
                raise ValueError('An AAAA record needs an IPv6 address, for example 2001:db8::1.') from None
        elif t in ('CNAME', 'NS', 'MX') and not HOSTNAME_RE.match(v):
            raise ValueError(f'{_article(t)} {t} record must point to a hostname, for example mail.example.com.')
        elif t == 'SRV':
            parts = v.split()
            if len(parts) != 3 or not all(p.isdigit() for p in parts[:2]) or not HOSTNAME_RE.match(parts[2]):
                raise ValueError('An SRV value is "weight port target", for example 5 5060 sip.example.com.')
        elif t == 'CAA' and not re.match(r'^\d{1,3}\s+(issue|issuewild|iodef)\s+\S+', v):
            raise ValueError('A CAA value is "flags tag value", for example 0 issue "letsencrypt.org".')
        if t in ('MX', 'SRV'):
            if self.priority is None:
                raise ValueError(f'{_article(t)} {t} record needs a priority (lower numbers are tried first).')
        else:
            self.priority = None
        return self

    def key(self) -> tuple:
        return self.type, self.name.lower(), self.value.lower().rstrip('.')


class DnsSyncState(Section):
    """What the live zone looked like at the last fetch; a push is refused if it has changed since."""
    provider: Literal['hostinger', 'namecheap'] = 'hostinger'
    fetched_at: Optional[datetime] = None
    fingerprint: str = ''
    unsupported: list[str] = Field(default_factory=list)  # live records kept as-is, e.g. URL redirects


class DnsConfig(Section):
    provider: str = ''  # who hosts the DNS zone; may differ from the registrar
    provider_url: str = ''
    nameservers: list[str] = Field(default_factory=list)
    records: list[DnsRecord] = Field(default_factory=list)
    sync: Optional[DnsSyncState] = None

    @field_validator('nameservers', mode='before')
    @classmethod
    def _split_nameservers(cls, value):
        if isinstance(value, str):
            value = re.split(r'[\s,;]+', value)
        return [ns.strip() for ns in value if ns and ns.strip()]

    @field_validator('nameservers')
    @classmethod
    def _check_nameservers(cls, value):
        bad = [ns for ns in value if not HOSTNAME_RE.match(ns)]
        if bad:
            raise ValueError(f'Not a valid nameserver hostname: {", ".join(bad)}')
        return value

    @model_validator(mode='after')
    def _check_zone(self):
        seen = set()
        for r in self.records:
            if r.key() in seen:
                raise ValueError(f'Duplicate {r.type} record for {r.name}: {r.value}')
            seen.add(r.key())
        cname_names = {r.name.lower() for r in self.records if r.type == 'CNAME'}
        for r in self.records:
            name = r.name.lower()
            if name in cname_names and r.type != 'CNAME':
                raise ValueError(f'{r.name} has a CNAME record, so it cannot also have a {r.type} record.')
        for name in cname_names:
            if sum(1 for r in self.records if r.type == 'CNAME' and r.name.lower() == name) > 1:
                raise ValueError(f'{name} can only have one CNAME record.')
        return self


class WebsiteConfig(Section):
    domain: Optional[DomainInfo] = None
    hosting: Optional[HostingInfo] = None
    dns: Optional[DnsConfig] = None


class CloudConfig(Section):
    provider: str = ''
    account: str = ''
    project: str = ''
    region: str = ''
    service: str = ''
    credential_ref: str = ''


class PaymentConfig(Section):
    provider: str = ''
    mode: str = ''
    account: str = ''
    webhook_url: str = ''
    credential_ref: str = ''


class AIConfig(Section):
    provider: str = ''
    model: str = ''
    endpoint: str = ''
    credential_ref: str = ''


class PathsConfig(Section):
    config: str = ''
    data: str = ''
    logs: str = ''
    artifacts: str = ''


class CommandsConfig(Section):
    start: str = ''
    restart: str = ''
    stop: str = ''


class ScanInfo(Section):
    scanned_at: Optional[datetime] = None
    file_count: int = 0
    modules: list[str] = Field(default_factory=list)
    clues: list[str] = Field(default_factory=list)


class Project(Section):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1)
    description: str = ''
    source_location: str = ''
    machine: str = ''
    status: str = 'Unknown'
    notes: str = ''
    date_created: date = Field(default_factory=date.today)
    project_type: ProjectType = 'app'
    size_mb: float = Field(default=0.0, ge=0)
    linked_items: list[str] = Field(default_factory=list)  # files and folders dropped onto the project
    database: Optional[DatabaseConfig] = None
    deployment: Optional[DeploymentConfig] = None
    website: Optional[WebsiteConfig] = None
    cloud: Optional[CloudConfig] = None
    payments: Optional[PaymentConfig] = None
    ai: Optional[AIConfig] = None
    paths: Optional[PathsConfig] = None
    commands: Optional[CommandsConfig] = None
    scan: Optional[ScanInfo] = None

    def is_blank(self) -> bool:
        return False

    @property
    def uses_database(self) -> bool:
        return self.database is not None and self.database.provider != 'none'

    @property
    def is_hosted(self) -> bool:
        return self.deployment is not None and self.deployment.environment == 'hosted'

    @property
    def is_website(self) -> bool:
        return self.project_type == 'website'

    @property
    def site_url(self) -> str:
        return self.deployment.site_url if self.deployment else ''

    @property
    def remote(self) -> Optional[SecretCredentials]:
        """SSH/server credentials, if this project has a remote host."""
        creds = self.deployment.credentials if self.deployment else None
        return creds if creds and creds.host else None

    @property
    def size_label(self) -> str:
        return human(self.size_mb * BYTES_PER_MB) if self.scan and self.scan.scanned_at else 'Not scanned'

    def command(self, name: str) -> str:
        return getattr(self.commands, name, '') if self.commands else ''

    def credential_slots(self) -> Iterator[tuple[str, SecretCredentials]]:
        if self.database and self.database.credentials:
            yield 'database', self.database.credentials
        if self.deployment and self.deployment.credentials:
            yield 'deployment', self.deployment.credentials

    def searchable_text(self) -> str:
        values = []
        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif node not in (None, '') and not isinstance(node, (list, bool)):
                values.append(str(node))
        walk(self.model_dump(mode='json', exclude={'scan'}))
        return ' '.join(values).lower()


class ProjectManager(Section):
    schema_version: int = 2
    projects: list[Project] = Field(default_factory=list)

    def __len__(self):
        return len(self.projects)

    def __getitem__(self, index: int) -> Project:
        return self.projects[index]

    def index_of(self, project_id: str) -> Optional[int]:
        return next((i for i, p in enumerate(self.projects) if p.id == project_id), None)

    def find(self, project_id: str) -> Optional[Project]:
        index = self.index_of(project_id)
        return None if index is None else self.projects[index]

    def add(self, project: Project) -> int:
        self.projects.append(project)
        return len(self.projects) - 1

    def remove(self, index: int) -> Project:
        return self.projects.pop(index)

    def replace(self, index: int, project: Project) -> None:
        self.projects[index] = project


def human(n):
    n = float(n or 0)
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024


def upcoming_renewals(projects, today=None):
    """Return (days_left, project, label, due_date) for website renewals whose reminder window has opened."""
    today = today or date.today()
    due = []
    for p in projects:
        if not p.is_website or not p.website:
            continue
        for label, item in (('Domain', p.website.domain), ('Hosting', p.website.hosting)):
            if not item or not item.expires_on or not (item.reminder and item.reminder.enabled):
                continue
            days_left = (item.expires_on - today).days
            if days_left <= item.reminder.days_before:
                due.append((days_left, p, label, item.expires_on))
    return sorted(due, key=lambda entry: (entry[0], entry[1].name.lower()))


def renewal_text(days_left):
    if days_left < 0:
        return f'OVERDUE by {-days_left} day(s)'
    if days_left == 0:
        return 'due TODAY'
    return f'due in {days_left} day(s)'


def get_path(data: dict, path: str, default=None):
    """Read 'a.b.c' from nested dicts produced by model_dump()."""
    node = data
    for part in path.split('.'):
        if not isinstance(node, dict) or node.get(part) is None:
            return default
        node = node[part]
    return node


def set_path(data: dict, path: str, value) -> None:
    """Write 'a.b.c' into nested dicts, creating (or replacing None) intermediates."""
    *parents, leaf = path.split('.')
    node = data
    for part in parents:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[leaf] = value
