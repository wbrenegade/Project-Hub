"""Conservative, local-only project configuration inference."""
from __future__ import annotations

import os
import re
from pathlib import Path

from project_models import Project, get_path, set_path

MAX_SOURCE_FILES = 3000
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
SOURCE_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.json', '.jsonc', '.toml',
    '.yaml', '.yml', '.prisma', '.go', '.rs', '.cs', '.php', '.java', '.rb',
    '.sql', '.html', '.vue', '.svelte',
}
SKIP_DIRS = {'tests', 'test', 'fixtures', 'examples', 'docs', 'vendor'}

# These match integration-specific identifiers, not generic words such as "sql" or "cloudflare".
RULES = {
    'database.provider': {
        'postgres': r'\b(?:postgres(?:ql)?|psycopg2?|asyncpg|pg8000|POSTGRES(?:QL)?_[A-Z0-9_]+)\b|["\']pg["\']\s*:|(?:from|require\s*\()\s*["\']pg["\']',
        'mysql': r'\b(?:mysql2?|pymysql|mariadb|MYSQL_[A-Z0-9_]+)\b',
        'sqlite': r'\b(?:sqlite3?|better-sqlite3|SQLITE_[A-Z0-9_]+)\b',
        'mongodb': r'\b(?:mongodb|mongoose|pymongo|MONGODB_[A-Z0-9_]+|MONGO_URI)\b',
        'mssql': r'\b(?:mssql|SQLSERVER_[A-Z0-9_]+|SQL_SERVER_[A-Z0-9_]+|Microsoft\.Data\.SqlClient)\b',
    },
    'cloud.service': {
        'Cloudflare R2': r'\b(?:R2_BUCKET(?:_NAME)?|R2_ACCOUNT_ID|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY|CLOUDFLARE_R2|r2\.cloudflarestorage\.com|@cloudflare/r2)\b',
        'Amazon S3': r'\b(?:AWS_S3_BUCKET|AWS_S3_BUCKET_NAME|s3\.amazonaws\.com)\b',
        'Supabase Storage': r'\b(?:supabase\.storage|SUPABASE_STORAGE_BUCKET)\b',
    },
    'payments.provider': {
        'Stripe': r'\b(?:STRIPE_[A-Z0-9_]+|@stripe/stripe-js|stripe-python|stripe\.checkout|stripe\.payments|from\s+stripe\s+import|import\s+stripe|require\s*\(\s*["\']stripe["\']|from\s+["\']stripe["\'])\b|["\']stripe["\']\s*:',
        'PayPal': r'\b(?:PAYPAL_[A-Z0-9_]+|@paypal/|paypalrestsdk)\b',
    },
    'ai.provider': {
        'OpenAI': r'\b(?:OPENAI_API_KEY|from\s+openai\s+import|import\s+openai|@ai-sdk/openai)\b',
        'Anthropic': r'\b(?:ANTHROPIC_API_KEY|from\s+anthropic\s+import|import\s+anthropic|@anthropic-ai/sdk)\b',
    },
}
HOST_FILES = {
    'vercel.json': 'Vercel', 'netlify.toml': 'Netlify',
    'render.yaml': 'Render', 'fly.toml': 'Fly.io',
}


def infer_project(path: str, ignored_dirs: set[str]) -> tuple[dict[str, str], list[str]]:
    """Return only recognized labels and generic evidence; never return source or env values."""
    root = Path(path)
    if not root.is_dir():
        raise ValueError('Folder is not available on this computer')
    matches: dict[str, set[str]] = {field: set() for field in RULES}
    hosts: set[str] = set()
    website = api = False
    files_read = total_bytes = errors = 0
    partial = False

    def walk_error(_error):
        nonlocal errors
        errors += 1

    for folder, dirs, names in os.walk(root, onerror=walk_error, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.lower() not in ignored_dirs
                         and d.lower() not in SKIP_DIRS and not (Path(folder) / d).is_symlink())
        for name in sorted(names):
            file = Path(folder) / name
            lower = name.lower()
            if file.is_symlink():
                continue
            if lower in {'package-lock.json', 'npm-shrinkwrap.json', 'composer.lock', 'yarn.lock', 'pnpm-lock.yaml'}:
                continue
            if lower in HOST_FILES:
                hosts.add(HOST_FILES[lower])
            if lower.endswith(('.db', '.sqlite', '.sqlite3')):
                matches['database.provider'].add('sqlite')
            env_file = lower == '.env' or lower.startswith('.env.')
            if not env_file and file.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            if files_read >= MAX_SOURCE_FILES or total_bytes >= MAX_TOTAL_BYTES:
                partial = True
                break
            limit = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_bytes)
            try:
                with file.open('rb') as handle:
                    content = handle.read(limit + 1)
            except OSError:
                errors += 1
                continue
            files_read += 1
            total_bytes += min(len(content), limit)
            if len(content) > limit:
                partial = True
                content = content[:limit]
            if b'\0' in content:
                continue
            text = content.decode('utf-8', errors='replace')
            if env_file:
                # Only variable names are used; values may contain passwords or tokens.
                text = '\n'.join(match.group(1) for line in text.splitlines()
                                 if (match := re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)))
            for field, values in RULES.items():
                for value, pattern in values.items():
                    if re.search(pattern, text, re.IGNORECASE):
                        matches[field].add(value)
            if not env_file:
                if lower in {'wrangler.toml', 'wrangler.json', 'wrangler.jsonc'}:
                    if re.search(r'\br2_buckets\b', text, re.IGNORECASE):
                        matches['cloud.service'].add('Cloudflare R2')
                    if re.search(r'\b(?:pages_build_output_dir|main)\b\s*["\']?\s*[:=]', text):
                        hosts.add('Cloudflare')
                if lower.endswith(('.html', '.vue', '.svelte', '.tsx', '.jsx')) or re.search(
                    r'["\'](?:next|react|vue|svelte|@angular/core)["\']\s*:', text
                ):
                    website = True
                if re.search(r'\b(?:fastapi|flask|django|express|nestjs|aspnetcore)\b', text, re.IGNORECASE):
                    api = True
        if partial and (files_read >= MAX_SOURCE_FILES or total_bytes >= MAX_TOTAL_BYTES):
            break

    fields: dict[str, str] = {}
    clues: list[str] = []
    for field, values in matches.items():
        if len(values) == 1:
            fields[field] = next(iter(values))
            clues.append(f'Detected {field}: {fields[field]} (review before use)')
        elif len(values) > 1:
            clues.append(f'Conflicting {field} clues: {", ".join(sorted(values))}; not filled')
    if website:
        fields['project_type'] = 'website'
    elif api:
        fields['project_type'] = 'api'
    if 'project_type' in fields:
        clues.append(f'Detected project type: {fields["project_type"]} (review before use)')
    if len(hosts) == 1:
        host = next(iter(hosts))
        fields.update({'deployment.environment': 'hosted', 'deployment.provider_name': host,
                       'deployment.platform': host, 'website.hosting.provider': host})
        clues.append(f'Detected deployment configuration: {host} (review before use)')
    elif len(hosts) > 1:
        clues.append(f'Conflicting deployment configurations: {", ".join(sorted(hosts))}; not filled')
    cloud_provider = {'Cloudflare R2': 'Cloudflare', 'Amazon S3': 'Amazon Web Services',
                      'Supabase Storage': 'Supabase'}
    if 'cloud.service' in fields:
        fields['cloud.provider'] = cloud_provider[fields['cloud.service']]
    if partial:
        clues.append('Configuration inference partial: source read limit reached')
    if errors:
        clues.append(f'Configuration inference partial: {errors} file or folder read error(s)')
    return fields, clues


def apply_inference(project: Project, fields: dict[str, str], original: dict,
                    infer_type: bool = True, protected: set[str] | None = None) -> Project:
    """Fill only unchanged, initially empty fields on a newly created project."""
    data = project.model_dump()
    protected = protected or set()
    cloud_provider = get_path(data, 'cloud.provider')
    cloud_service = get_path(data, 'cloud.service')
    cloud_conflict = ((cloud_provider and cloud_provider != fields.get('cloud.provider'))
                      or (cloud_service and cloud_service != fields.get('cloud.service')))
    host = fields.get('deployment.provider_name')
    host_conflict = any(value and value != host for value in (
        get_path(data, 'deployment.provider_name'), get_path(data, 'deployment.platform'),
        get_path(data, 'website.hosting.provider'))) if host else False
    host_conflict = host_conflict or ('deployment.environment' in protected
                                      and get_path(data, 'deployment.environment', 'local') == 'local')
    for path, value in fields.items():
        if (path in protected or (path.startswith('cloud.') and cloud_conflict)
                or (path in {'deployment.environment', 'deployment.provider_name', 'deployment.platform',
                             'website.hosting.provider'} and host_conflict)):
            continue
        before = get_path(original, path)
        current = get_path(data, path)
        if path == 'project_type':
            eligible = infer_type and before == 'app' and current == before
        elif path == 'deployment.environment':
            eligible = before in (None, '', 'local') and current == before
        else:
            eligible = before in (None, '', 'none') and current == before
        if eligible:
            set_path(data, path, value)
    updated = Project.model_validate(data)
    for section in ('database', 'deployment'):
        old_config = getattr(project, section)
        new_config = getattr(updated, section)
        if old_config and old_config.credentials:
            if new_config is None:
                setattr(updated, section, old_config.model_copy(deep=True))
            else:
                new_config.credentials = old_config.credentials.model_copy(deep=True)
    return updated
