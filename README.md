# Osmia v2

A self-hosted no-code database in the spirit of NocoDB, built on Django. Staff users build
**apps** in the browser: tables, fields (text, numbers, selects, links between rows, images,
per-category attributes, ...) and **views** (saved grids with columns, filters and sorting).
Everyone else works with the data: grids with inline editing, row pages and forms.

Everything that is built is described by **blueprints** (JSON). The builder pages, the AI
import and the ready-made apps all apply blueprints, so an AI can extend Osmia the same way a
person does.

## Quick start (Windows PowerShell)

```powershell
cd "C:\python scripts\osmia-v2"
python -m venv .venv                      # skip if .venv exists
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000, log in, and click **Install…** under *Ready-made apps* to add
**Parts**. (Or from the command line: `.\.venv\Scripts\python manage.py import_blueprint blueprints\parts.json`.)

Add users in sidebar → **Users**. Tick *Staff status* for people who may build (change apps,
tables, fields, views, import blueprints). Others can add and edit rows.

## Parts

The parts catalogue, as in Osmia, installed from `blueprints/parts.json`:

- **Parts**: part number (unique, ignoring case), description, nested category, the category's
  **attributes**, unit (default *pcs*), images, **Mates with** (two-way: a 9-pin plug mates with a
  9-socket receptacle), **Accepts** (one-way: a housing accepts its contacts; the contact's page
  lists the housing under **Fits into**), Active (untick to archive) and notes.
  Views: *Active parts*, *Archived*, *All parts*.
- **Categories**: nested (e.g. Hardware › Fasteners). Each lists the attributes its parts have,
  with a hint and whether it's required. Sub-categories inherit their parents' attributes. A
  category's page lists its sub-categories and its parts (including those of sub-categories).
- Search finds parts by part number, description, category name and attribute values.

Not included from the old Osmia: stock, tools, the Mouser lookup and the other modules.

## Building in the browser

- **+ New app**, then **+ Table** on the app page. A new table starts with a *Name* field.
- On a table: **+ Field** adds a column (pick the type; its options appear), ✎ on a column header
  edits a field, **Table settings** sets which field names a row, a second line for pickers,
  nesting (a *Parent* link to the same table), field order, and deletes fields or the table.
- **⚙ Edit view** / **+ View**: which columns, their order, filters and sorting.
- Grid: double-click a cell to edit it (Enter saves, Esc cancels). Click a row number to open
  the row: its values, images (upload, caption, cover), and the rows that link to it.

## Building with AI

1. Sidebar → **✨ AI builder**: describe what you want, **Make prompt**. The prompt holds
   `AI_GUIDE.md` (the blueprint format), your current setup and your request.
2. Give it to any AI (Copilot, ChatGPT, Claude, ...). Too long to paste? **Download** it and attach
   the file.
3. Sidebar → **⤓ Import blueprint**: upload the AI's answer (a .json, .txt or .md file, or the
   whole answer pasted in). Osmia shows every change first; **Apply** makes them.
4. Problems? Nothing is changed. **Copy errors for the AI**, send them back, import the answer.

Blueprints never delete anything. **Export** on an app page downloads its blueprint (with or
without data) for backups or for moving an app to another Osmia.

## Project layout

```
config/          Django settings and URLs
builder/         the whole engine
  models.py        App, Table, Field, View, Row (values in JSON), Link, Attachment
  fieldtypes.py    field types: checking, storing, showing values
  records.py       saving rows (all rules), labels, search, filters, nesting
  blueprint.py     apply / export blueprints (the only way schema changes)
  schema.py        keys, view settings, deleting
  views_*.py       pages: data, building, AI
blueprints/      ready-made apps (parts.json), shown on the home page
AI_GUIDE.md      the blueprint format, given to AIs in every prompt
```

## Tests

```powershell
.\.venv\Scripts\python manage.py test builder
```

## Settings

Environment variables: `OSMIA_SECRET_KEY`, `OSMIA_DEBUG=0` and `OSMIA_ALLOWED_HOSTS` for real use;
`OSMIA_DB_PATH` (default `db.sqlite3`) and `OSMIA_MEDIA_ROOT` (default `media/`, the uploaded
images). Back up both before updating. For production run `manage.py collectstatic` and serve
with a WSGI server (e.g. waitress).
