# Project Hub

A Windows-friendly local project organizer built with Python and PySide6.

## Run

```powershell
pip install PySide6 "pydantic>=2"
python project_hub.py
```

`project_hub.py` remains the application launcher. The implementation is split into focused modules:

- `project_app.py` — main window and application startup.
- `project_models.py` — strongly typed, nested Pydantic v2 models (`ProjectManager` → `Project` → `DatabaseConfig`, `DeploymentConfig`, `SecretCredentials`, website/cloud/payments/AI sections) and renewal helpers.
- `project_storage.py` — JSON persistence via `model_dump_json()` / `model_validate_json()`, migration of the old flat format, and the encrypted credential vault.
- `project_dns_sync.py` — fetching, diffing and pushing DNS records to Hostinger and Namecheap, with backups and stale-data checks.
- `project_services.py` — folder scanning, site checks, SEO auditing, scan workers, and SSH/SCP helpers.
- `project_dialogs.py` — dialogs and shared UI styling.
- `project_map.py` — the mind-map view of projects, grouped as in `PROJECT_FIELD_GROUPING.md`.

## Features

- **Map view** (default on the work page; `List view` switches back to the table): each project is a draggable folder. Click it to open its categories, click a category for its sub-categories, and a sub-category for its fields. Dragging a node carries its open branch with it; drag the background to pan and use the mouse wheel to zoom. Double-click a project to edit it. Folder positions are saved to `%LOCALAPPDATA%\ProjectHub\map_layout.json`.
- Add projects by scanning a folder or manually entering details.
- Remove a project from the inventory using **Delete project** or its right-click menu in the map or list. Confirmation is required; project files are not deleted.
- When a new project has a local folder, a background scan reads bounded portions of source/configuration files and environment variable **names** to suggest its type, database, storage, payments, AI and deployment providers. Recognized values fill empty fields; existing or subsequently edited values are preserved. Review the project and the scan clues: these are guesses, not verified services. Conflicting clues leave a field blank. The scan does not infer a domain registrar from a DNS, hosting, or storage provider, and never saves environment values or API keys. Later automatic/manual rescans update the inventory, not configuration.
- Keep track of local folder, machine, project purpose, status and notes.
- Track website, domain, health URL, server host/port/user, remote path, deployment platform, runtime, environment, and service/container name.
- Connect **Hostinger** and **Namecheap** accounts (via `Tools -> Connected accounts (APIs)` or `Project -> Import from Hostinger / Namecheap...`) to live-query registered domains, renewal/expiration dates, auto-renew settings, and Hostinger VPS details. Automatically autofills project website and server fields. API credentials are encrypted with Windows DPAPI.
- Mark a project as a **website** to record domain details (name, registrar / purchase site, registration and renewal dates, cost, auto-renew) and hosting details (provider / purchase site, plan, IP address, purchase and renewal dates, cost, auto-renew).
- Website projects can also record their **DNS provider** (which may differ from the registrar), nameservers, and **DNS records** (A, AAAA, CNAME, MX, TXT, NS, SRV, CAA). Add, edit, or remove records in the project editor's Website tab or via `Site -> DNS records...`. Each record is validated for its type (e.g. A needs an IPv4 address, MX needs a priority), and conflicts such as duplicates or a CNAME sharing a name with other records are rejected. Records are kept locally until you push them.
- **Sync DNS with Hostinger or Namecheap** from `Site -> DNS records...`: **Fetch from provider** reads the live zone, and **Push to provider...** writes your edits back. The provider is taken from the Website tab's "DNS provider" (falling back to the registrar) and must be where the domain's nameservers point. Safeguards:
  - Pushing requires a prior fetch. The live zone is re-read right before writing, and the push is refused if it changed since that fetch.
  - A preview lists every addition, removal and change, and nothing is sent until you confirm.
  - The live zone is saved to `%LOCALAPPDATA%\ProjectHub\dns-backups\` before every push, then re-read afterwards so the list shows exactly what is live.
  - Live records Project Hub can't edit (URL redirects, ALIAS, disabled records) are always sent back unchanged.
  - Namecheap: setHosts replaces the whole host list, so the full list is always sent. SRV records and TTLs outside 60-60000 are refused, and MX changes are refused while Namecheap Email Forwarding or Private Email manages the domain's mail.
- Optionally set a **renewal reminder** (N days before, default 30) for the domain and/or hosting. Due and overdue renewals are listed on the Home page (double-click to open the project) and shown in an alert when Project Hub starts. Dates use `YYYY-MM-DD`; reminders only work while the app is opened, and nothing is sent externally.
- Record cloud provider/account/project/region/service details, payment provider/mode/account/webhook details, and AI provider/model/endpoint details.
- Track local configuration, data, log, and build/artifact locations.
- Store credentials in a separate vault encrypted with Windows DPAPI for the current Windows user; put the matching vault key (not the secret) in a project's server, cloud, payments, or AI credential field.
- Retrieve secrets only through the Credentials window, which keeps values masked until you explicitly reveal them. The vault is not included in project JSON exports.
- Track start, restart, and stop commands; copy them to the clipboard.
- Optionally launch a **start** command on the local computer only, with explicit confirmation and a new terminal window. Remote commands are not executed.
- Scan local project size and file count, excluding bulky generated dependency folders (`node_modules`, virtual environments, `.git`, `dist`, etc.). This is *source/project footprint*, **not total disk usage**.
- Discover dependencies from `requirements.txt`, `pyproject.toml`, and `package.json`; detect common framework/deployment files.
- Search projects and export the inventory to JSON.
- Stores project records locally under `%LOCALAPPDATA%\ProjectHub\projects.json` on Windows. Nothing is sent to a server.

## Important limits

- Does not connect to servers, discover live running processes, or verify the actual deployment automatically. The exceptions are the connected Hostinger/Namecheap account features, including DNS push, which changes live DNS only when you confirm it.
- Modules and deployment clues are *detected from files*, not definitive runtime facts.
- Project inventory fields and exports are local plaintext; do not put secret values in them. The separate credential vault is encrypted for the Windows user account that created it and cannot be decrypted by another account or on another computer. Protect access to your Windows account and device.
- Credential values are decrypted in application memory while the vault window is open. Revealing a credential displays it on screen; close or hide the window when finished. Project Hub does not inject credentials into commands or send them to servers.
- Encrypted credential storage requires Windows DPAPI. The credential manager is unavailable on other platforms; no plaintext fallback is used.
- Folder scanner skips dependency/build directories and caps at 300,000 files. A capped scan is marked partial.
- Configuration inference also skips tests, examples, documentation, symlinks, and common lock files; it reads at most 3,000 source files, 128 KiB per file, and 20 MiB in total. Partial scans and read errors are reported in the scan clues. Supported database choices include PostgreSQL, MySQL, SQLite, MongoDB and SQL Server; ambiguous or unsupported databases are not guessed.
- Start command is run using your computer's shell. Only run commands that you personally trust.
- An inventory in the old flat format is migrated automatically on first start; the original is kept as `projects.v1-backup-<timestamp>.json`. A file that fails validation is copied to `projects.unreadable-<timestamp>.json` before the app starts with an empty inventory.
- Database and deployment passwords typed in the project editor are moved into the encrypted vault on save and never written to `projects.json`; the project stores only the vault key.
- Existing data is backed by an atomic JSON write. Make separate backups of your inventory if important.

## Next possible improvements

Actual disk usage switch, clickable service dashboards, process monitoring, Git branch/status, docker inspections, SSH health probes, editable command templates, and parent/child project grouping.
