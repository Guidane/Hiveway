# Working on Osmia v2

## Add features through the builder system

When asked to add something (a table, a field, a new app, a list of data, a change to how
something is organised), express it as a **blueprint**, not as Django code:

- A new ready-made app or a change to one: edit or add a file in `blueprints/` (format in
  `AI_GUIDE.md`) and check it with `manage.py import_blueprint blueprints/<file>.json --dry-run`.
- Users install or update it from the home page (*Ready-made apps*), which previews and applies it.
- Never add Django models, migrations or app-specific views for things a blueprint can express.

Only change the engine (`builder/`) when the blueprint format can't express what is needed
(e.g. a new field type, formulas, automations). Then:

1. Make the feature generic (usable by any app), not specific to one table.
2. Extend the blueprint format for it (`blueprint.py` apply + export) so AIs can use it too.
3. Document it in `AI_GUIDE.md` (that file is sent to AIs in every prompt) and the README.
4. Add tests in `builder/tests.py`.

## Rules of the engine

- All row writes go through `records.save_row`; all schema creates/changes go through
  `blueprint.apply` (the builder pages send small blueprints). Deletes live in `schema.py`.
- Keys (app, table, field, view) never change after creation; names can.
- Blueprints never delete and are all-or-nothing (one transaction; dry runs roll back).

## Commands

```powershell
.\.venv\Scripts\python manage.py test builder
.\.venv\Scripts\python manage.py import_blueprint blueprints\parts.json --dry-run
.\.venv\Scripts\python manage.py export_blueprint --app parts
```
