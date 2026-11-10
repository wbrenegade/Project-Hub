"""Fetch live DNS records from, and push local edits to, Hostinger and Namecheap.

Safety rules:
- A push must start from a fetch: the live zone is fetched again right before writing, and the push
  is refused if it no longer matches the fingerprint taken at the last fetch.
- Records Project Hub cannot represent (URL redirects, ALIAS, disabled records, ...) are carried
  through unchanged, because Namecheap's setHosts replaces the whole zone.
- The live zone is backed up to disk before anything is written.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from project_models import DnsRecord, DnsSyncState
from project_services import detect_public_ip, hostinger_request, namecheap_request
from project_storage import DATA

BACKUP_DIR = DATA.with_name('dns-backups')
HOST_TYPES = {'CNAME', 'NS', 'MX', 'SRV'}
NAMECHEAP_TYPES = {'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'CAA'}  # SRV cannot be set through setHosts
NAMECHEAP_TTL = (60, 60000)
NAMECHEAP_MAIL = {'FWD': 'Free Email Forwarding', 'OX': 'Private Email', 'MXE': 'MXE forwarding'}
PROVIDER_LABELS = {'hostinger': 'Hostinger', 'namecheap': 'Namecheap'}


class DnsSyncError(Exception):
    pass


@dataclass
class LiveZone:
    provider: str
    domain: str
    records: list[DnsRecord]
    passthrough: list[dict]  # provider-native records Project Hub can't edit; always sent back unchanged
    fingerprint: str
    raw: object
    email_type: str = ''  # Namecheap mail setting (MX, FWD, OX, MXE)

    def sync_state(self) -> DnsSyncState:
        return DnsSyncState(provider=self.provider, fetched_at=datetime.now().replace(microsecond=0),
                            fingerprint=self.fingerprint, unsupported=[describe_passthrough(p) for p in self.passthrough])


@dataclass
class PushPlan:
    zone: LiveZone
    local: list[DnsRecord]
    added: list[DnsRecord] = field(default_factory=list)
    removed: list[DnsRecord] = field(default_factory=list)
    changed: list[tuple[DnsRecord, DnsRecord]] = field(default_factory=list)  # (live, local)
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)


def describe_passthrough(p: dict) -> str:
    value = p.get('content', p.get('address', ''))
    disabled = ' (disabled)' if p.get('is_disabled') else ''
    return f"{p['type']} {p['name']} {value}{disabled}".strip()


def norm_value(r: DnsRecord) -> str:
    value = r.value.strip()
    if r.type == 'TXT' and len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if r.type in HOST_TYPES:
        value = value.rstrip('.').lower()
    return value


def record_key(r: DnsRecord) -> tuple:
    return r.type, r.name.lower(), norm_value(r)


def fingerprint(entries: list) -> str:
    canonical = json.dumps(sorted(json.dumps(e, sort_keys=True, default=str) for e in entries))
    return hashlib.sha256(canonical.encode('utf8')).hexdigest()


def sync_target(project) -> tuple[str, str]:
    """Return (provider key, domain) for a website project, or raise with what is missing."""
    website = project.website
    domain = website.domain.name if website and website.domain else ''
    if not project.is_website or not domain:
        raise DnsSyncError('Set the domain name in the Website tab before syncing DNS.')
    dns_provider = website.dns.provider if website.dns else ''
    name = (dns_provider or website.domain.registrar or '').lower()
    for key in PROVIDER_LABELS:
        if key in name:
            return key, domain
    raise DnsSyncError('Project Hub can sync DNS with Hostinger and Namecheap. Set "DNS provider" in the '
                       "Website tab to the company your domain's nameservers point to.")


def provider_credentials(provider: str, credentials: dict) -> dict:
    if provider == 'hostinger':
        if not credentials.get('hostinger_api_token'):
            raise DnsSyncError('Add your Hostinger API token under Tools -> Connected accounts first.')
        return {'token': credentials['hostinger_api_token']}
    if not (credentials.get('namecheap_username') and credentials.get('namecheap_api_key')):
        raise DnsSyncError('Add your Namecheap API username and key under Tools -> Connected accounts first.')
    return {'username': credentials['namecheap_username'], 'api_key': credentials['namecheap_api_key'],
            'client_ip': credentials.get('namecheap_client_ip') or detect_public_ip()}


def fetch_live(provider: str, domain: str, credentials: dict) -> LiveZone:
    creds = provider_credentials(provider, credentials)
    return hostinger_fetch(creds, domain) if provider == 'hostinger' else namecheap_fetch(creds, domain)


def build_plan(zone: LiveZone, local: list[DnsRecord]) -> PushPlan:
    live = {record_key(r): r for r in zone.records}
    mine = {record_key(r): r for r in local}
    plan = PushPlan(zone=zone, local=list(local))
    plan.added = [r for k, r in mine.items() if k not in live]
    plan.removed = [r for k, r in live.items() if k not in mine]
    plan.changed = [(live[k], r) for k, r in mine.items()
                    if k in live and (live[k].ttl, live[k].priority) != (r.ttl, r.priority)]
    if zone.passthrough:
        plan.warnings.append(f'{len(zone.passthrough)} live record(s) Project Hub cannot edit will be kept unchanged: '
                             + '; '.join(describe_passthrough(p) for p in zone.passthrough[:5])
                             + (' ...' if len(zone.passthrough) > 5 else ''))
    (_check_hostinger if zone.provider == 'hostinger' else _check_namecheap)(plan)
    return plan


def prepare_push(provider: str, domain: str, sync: DnsSyncState | None, local: list[DnsRecord], credentials: dict) -> PushPlan:
    if not sync or not sync.fingerprint:
        raise DnsSyncError('Fetch the live records from your provider first, then edit and push.')
    if sync.provider != provider:
        raise DnsSyncError(f'These records were last fetched from {PROVIDER_LABELS[sync.provider]}, but the DNS provider '
                           f'is now {PROVIDER_LABELS[provider]}. Fetch again before pushing.')
    zone = fetch_live(provider, domain, credentials)
    if zone.fingerprint != sync.fingerprint:
        raise DnsSyncError(f'The live records at {PROVIDER_LABELS[provider]} changed since your last fetch '
                           f'({sync.fetched_at:%Y-%m-%d %H:%M}). Nothing was pushed.\n\n'
                           'Fetch again to see the current records, re-apply your edits, then push.')
    return build_plan(zone, local)


def apply_push(plan: PushPlan, credentials: dict, backup_dir: Path = BACKUP_DIR) -> tuple[LiveZone, Path]:
    """Back up the live zone, write the plan, and return the zone as fetched afterwards."""
    if plan.empty:
        raise DnsSyncError('There are no changes to push.')
    zone = plan.zone
    backup = write_backup(zone, backup_dir)
    creds = provider_credentials(zone.provider, credentials)
    try:
        (hostinger_apply if zone.provider == 'hostinger' else namecheap_apply)(creds, plan)
    except Exception as exc:
        raise DnsSyncError(f'{exc}\n\nThe live records from before this push were saved to:\n{backup}') from exc
    return fetch_live(zone.provider, zone.domain, credentials), backup


def write_backup(zone: LiveZone, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f'{zone.domain}-{zone.provider}-{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps({'provider': zone.provider, 'domain': zone.domain, 'email_type': zone.email_type,
                                'saved_at': datetime.now().isoformat(timespec='seconds'), 'raw': zone.raw},
                               indent=2, default=str), encoding='utf8')
    return path


def _parse(type_, name, value, ttl, priority=None):
    try:
        return DnsRecord(type=type_, name=name or '@', value=value, ttl=ttl, priority=priority)
    except ValidationError:
        return None


# Hostinger: zone is a list of record sets {name, type, ttl, records: [{content, is_disabled}]}.
# PUT with overwrite=true replaces only the record sets it names; DELETE removes record sets by name and type.

def _hostinger_path(domain, suffix=''):
    return f'/api/dns/v1/zones/{quote(domain)}{suffix}'


def hostinger_fetch(creds: dict, domain: str) -> LiveZone:
    data = hostinger_request(creds['token'], 'GET', _hostinger_path(domain)) or []
    if isinstance(data, dict):
        data = data.get('data', [])
    records, passthrough, entries = [], [], []
    for rrset in data:
        name, type_, ttl = rrset.get('name') or '@', rrset.get('type', ''), rrset.get('ttl', 3600)
        for rec in rrset.get('records', []):
            content, disabled = rec.get('content', ''), bool(rec.get('is_disabled'))
            entries.append([name, type_, ttl, content, disabled])
            parsed = None
            if not disabled:
                priority, value = None, content
                if type_ in ('MX', 'SRV'):
                    head, _, rest = content.partition(' ')
                    priority, value = (int(head), rest.strip()) if head.isdigit() else (None, '')
                parsed = _parse(type_, name, value, ttl, priority)
            if parsed:
                records.append(parsed)
            else:
                passthrough.append({'name': name, 'type': type_, 'ttl': ttl, 'content': content, 'is_disabled': disabled})
    return LiveZone('hostinger', domain, records, passthrough, fingerprint(entries), data)


def _hostinger_content(r: DnsRecord) -> str:
    return f'{r.priority} {r.value}' if r.type in ('MX', 'SRV') else r.value


def _rrset(r_or_p):
    if isinstance(r_or_p, DnsRecord):
        return r_or_p.type, r_or_p.name.lower()
    return r_or_p['type'], r_or_p['name'].lower()


def _check_hostinger(plan: PushPlan):
    groups = {}
    for r in plan.local:
        groups.setdefault(_rrset(r), []).append(r)
    for (type_, name), group in groups.items():
        if len({r.ttl for r in group}) > 1:
            plan.warnings.append(f'Hostinger uses one TTL per name and type: the {type_} records for {name} '
                                 f'will all use {min(r.ttl for r in group)} seconds.')


def hostinger_apply(creds: dict, plan: PushPlan):
    touched = {_rrset(r) for r in plan.added + plan.removed} | {_rrset(new) for _, new in plan.changed}
    put, delete = [], []
    for key in sorted(touched):
        group = [r for r in plan.local if _rrset(r) == key]
        kept = [p for p in plan.zone.passthrough if _rrset(p) == key]
        if group or kept:
            put.append({'name': (group[0].name if group else kept[0]['name']), 'type': key[0],
                        'ttl': min(r.ttl for r in group) if group else kept[0]['ttl'],
                        'records': [{'content': _hostinger_content(r)} for r in group]
                                   + [{'content': p['content'], 'is_disabled': p['is_disabled']} for p in kept]})
        else:
            name = next(r.name for r in plan.removed if _rrset(r) == key)
            delete.append({'name': name, 'type': key[0]})
    path = _hostinger_path(plan.zone.domain)
    body = {'overwrite': True, 'zone': put}
    if put and not delete:
        # Validation runs against the current zone, so it is only meaningful when nothing is deleted first
        # (e.g. replacing an A record with a CNAME of the same name would fail before the delete).
        hostinger_request(creds['token'], 'POST', _hostinger_path(plan.zone.domain, '/validate'), body)
    if delete:
        hostinger_request(creds['token'], 'DELETE', path, {'filters': delete})
    if put:
        hostinger_request(creds['token'], 'PUT', path, body)


# Namecheap: getHosts returns every host; setHosts replaces the entire host list with the one sent.

def _split_domain(domain: str) -> dict:
    sld, _, tld = domain.strip().lower().partition('.')
    if not sld or not tld:
        raise DnsSyncError(f'"{domain}" is not a full domain name (expected something like example.com).')
    return {'SLD': sld, 'TLD': tld}


def _xml_children(root, tag):
    return [el for el in root.iter() if el.tag.split('}')[-1].lower() == tag.lower()]


def namecheap_fetch(creds: dict, domain: str) -> LiveZone:
    root = namecheap_request(creds['username'], creds['api_key'], creds['client_ip'],
                             'namecheap.domains.dns.getHosts', _split_domain(domain))
    results = _xml_children(root, 'DomainDNSGetHostsResult')
    if not results:
        raise DnsSyncError('Namecheap returned no DNS host list for this domain.')
    result = results[0]
    if result.attrib.get('IsUsingOurDNS', '').lower() != 'true':
        raise DnsSyncError(f"DNS for {domain} isn't hosted at Namecheap: its nameservers point to another provider. "
                           'Set "DNS provider" to that provider instead.')
    records, passthrough, entries, raw = [], [], [], []
    for host in _xml_children(result, 'host'):
        a = dict(host.attrib)
        raw.append(a)
        name, type_, address = a.get('Name') or '@', a.get('Type', ''), a.get('Address', '')
        ttl = int(a['TTL']) if a.get('TTL', '').isdigit() else 1800
        mxpref = int(a['MXPref']) if a.get('MXPref', '').isdigit() else 10
        entries.append([name, type_, address, ttl, mxpref if type_ == 'MX' else None])
        parsed = _parse(type_, name, address, ttl, mxpref if type_ == 'MX' else None) if type_ in NAMECHEAP_TYPES else None
        if parsed:
            records.append(parsed)
        else:
            passthrough.append({'name': name, 'type': type_, 'address': address, 'ttl': ttl, 'mxpref': mxpref})
    email_type = result.attrib.get('EmailType', '')
    entries.append(['EmailType', email_type])
    return LiveZone('namecheap', domain, records, passthrough, fingerprint(entries), raw, email_type)


def _check_namecheap(plan: PushPlan):
    problems = []
    srv = [r for r in plan.local if r.type == 'SRV']
    if srv:
        problems.append("Namecheap's API cannot set SRV records. Remove them here and manage them in the Namecheap dashboard.")
    low, high = NAMECHEAP_TTL
    bad_ttl = [r for r in plan.local if not low <= r.ttl <= high]
    if bad_ttl:
        problems.append(f'Namecheap TTLs must be between {low} and {high} seconds: '
                        + ', '.join(f'{r.type} {r.name} ({r.ttl})' for r in bad_ttl))
    mail = NAMECHEAP_MAIL.get(plan.zone.email_type)
    mx_touched = any(r.type == 'MX' for r in plan.added + plan.removed + [new for _, new in plan.changed])
    if mail and mx_touched:
        problems.append(f"This domain's mail is set to Namecheap {mail}. Change mail (MX) settings in the "
                        'Namecheap dashboard instead, or switch it to custom MX there first.')
    if problems:
        raise DnsSyncError('Cannot push to Namecheap:\n\n' + '\n\n'.join(problems))
    plan.warnings.append('Namecheap replaces the whole host list on every push; all records shown above are sent together.')
    if mail:
        plan.warnings.append(f'Mail setting "{mail}" is kept as it is.')


def namecheap_apply(creds: dict, plan: PushPlan):
    # When Namecheap runs the mail (forwarding / Private Email), it owns the MX records: keep its mail
    # setting and leave MX out, rather than switching the domain to custom MX.
    managed_mail = plan.zone.email_type in NAMECHEAP_MAIL
    hosts = [(r.name, r.type, r.value, r.priority, r.ttl) for r in plan.local if not (managed_mail and r.type == 'MX')]
    hosts += [(p['name'], p['type'], p['address'], p['mxpref'], p['ttl']) for p in plan.zone.passthrough]
    params = _split_domain(plan.zone.domain)
    for i, (name, type_, address, mxpref, ttl) in enumerate(hosts, 1):
        params.update({f'HostName{i}': name, f'RecordType{i}': type_, f'Address{i}': address, f'TTL{i}': str(ttl)})
        if type_ == 'MX':
            params[f'MXPref{i}'] = str(mxpref if mxpref is not None else 10)
    if managed_mail:
        params['EmailType'] = plan.zone.email_type
    elif any(type_ == 'MX' for _, type_, *_ in hosts):
        params['EmailType'] = 'MX'
    elif plan.zone.email_type:
        params['EmailType'] = plan.zone.email_type
    root = namecheap_request(creds['username'], creds['api_key'], creds['client_ip'],
                             'namecheap.domains.dns.setHosts', params, post=True)
    results = _xml_children(root, 'DomainDNSSetHostsResult')
    if not results or results[0].attrib.get('IsSuccess', '').lower() != 'true':
        raise DnsSyncError('Namecheap did not confirm the update (IsSuccess was not true).')
