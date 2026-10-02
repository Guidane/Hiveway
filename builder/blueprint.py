"""Blueprints: apps, tables, fields, views and rows described as JSON.

Every change to what users have built goes through ``apply``: the builder
pages send small blueprints, AI answers are blueprints, and ready-made apps
(blueprints/parts.json) are blueprints. AI_GUIDE.md documents the format.

Rules: things are matched by key and created or updated; what a blueprint
leaves out stays as it is; nothing is ever deleted. ``apply`` runs inside one
transaction, so a blueprint with any error changes nothing, and a dry run
shows exactly what applying would do.
"""
import json
import re
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from . import fieldtypes, records, schema
from .models import KEY_PATTERN, App, Field, Table, View

VERSION = 1

# Type changes allowed on a field that already has values.
SAFE_TYPE_CHANGES = {
    ('text', 'long_text'), ('long_text', 'text'), ('url', 'text'), ('email', 'text'),
    ('select', 'text'), ('url', 'long_text'), ('email', 'long_text'), ('select', 'long_text'),
}


class Result:
    def __init__(self):
        self.changes = []
        self.errors = []
        self.notes = ''
        self.applied = False
        self.row_counts = Counter()

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        lines = list(self.changes)
        for (table, what), n in sorted(self.row_counts.items()):
            lines.append(f'{table}: {n} {what} row{"s" if n != 1 else ""}')
        return lines

    def errors_for_ai(self):
        lines = '\n'.join(f'- {e}' for e in self.errors)
        return ('Osmia could not apply your blueprint. Nothing was changed. Fix these problems and send the '
                f'whole corrected blueprint again as one ```json block:\n{lines}')


class _Rollback(Exception):
    pass


def apply(data, user=None, dry_run=False):
    """Apply a blueprint (already parsed JSON). Returns a Result."""
    result = Result()
    try:
        with transaction.atomic():
            _Importer(result, user).run(data)
            if result.errors or dry_run:
                raise _Rollback()
    except _Rollback:
        pass
    else:
        result.applied = True
    return result


def parse_text(text):
    """The blueprint JSON in ``text``: plain JSON, or an AI answer with the
    JSON in a ```json block. Raises ValueError with a readable message."""
    text = (text or '').strip().lstrip('﻿')
    if not text:
        raise ValueError('The file or text is empty.')
    candidates = [text]
    blocks = re.findall(r'```[a-zA-Z]*[ \t]*\r?\n(.*?)```', text, re.S)
    blocks.sort(key=lambda b: ('osmia_blueprint' not in b, -len(b)))
    candidates += blocks
    start, end = text.find('{'), text.rfind('}')
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    last_error = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
    if last_error:
        raise ValueError(f'The JSON is not valid: {last_error}. Ask the AI for the blueprint again as one ```json block.')
    raise ValueError('No blueprint found. It should be JSON starting with {"osmia_blueprint": 1, ...}.')


def _text(value, limit):
    return ' '.join(str(value if value is not None else '').split())[:limit]


class _Importer:
    def __init__(self, result, user):
        self.result = result
        self.user = user

    # -- helpers ---------------------------------------------------------------

    def error(self, where, message):
        self.result.errors.append(f'{where}: {message}' if where else message)

    def change(self, message):
        self.result.changes.append(message)

    def check_keys(self, obj, allowed, where):
        unknown = sorted(set(obj) - set(allowed))
        if unknown:
            self.error(where, f'unknown properties {", ".join(unknown)}. Allowed: {", ".join(sorted(allowed))}.')

    def list_of(self, obj, name, where):
        value = obj.get(name) if isinstance(obj, dict) else None
        if value is None:
            return []
        if not isinstance(value, list):
            self.error(where, f'"{name}" must be a list.')
            return []
        return value

    def valid_key(self, obj, where):
        key = obj.get('key') if isinstance(obj, dict) else None
        if not isinstance(key, str) or not re.match(KEY_PATTERN, key) or len(key) > 50:
            self.error(where, f'"key" must be lower-case letters, digits and single underscores, starting with a '
                              f'letter (got {json.dumps(key)}).')
            return None
        return key

    def update_attrs(self, obj, spec, limits):
        """Copy the properties present in ``spec`` onto ``obj``; returns the changed names."""
        changed = []
        for name, (attr, limit) in limits.items():
            if name not in spec:
                continue
            value = str(spec[name] or '').strip()[:limit] if attr == 'description' else _text(spec[name], limit)
            if attr == 'name' and not value:
                continue
            if getattr(obj, attr) != value:
                setattr(obj, attr, value)
                changed.append(name)
        return changed

    # -- run -------------------------------------------------------------------

    def run(self, data):
        if not isinstance(data, dict):
            return self.error('', 'The blueprint must be a JSON object.')
        if data.get('osmia_blueprint') != VERSION:
            return self.error('', f'Put "osmia_blueprint": {VERSION} at the top of the JSON.')
        self.check_keys(data, {'osmia_blueprint', 'notes', 'apps', 'rows'}, 'blueprint')
        self.result.notes = str(data.get('notes') or '').strip()

        # Apps and tables first, so links can point at tables made in this same blueprint.
        plans, broken = [], False
        for i, adef in enumerate(self.list_of(data, 'apps', 'blueprint')):
            where = f'apps[{i}]'
            app = self.upsert_app(adef, where)
            if app is None:
                broken = True
                continue
            for j, tdef in enumerate(self.list_of(adef, 'tables', where)):
                twhere = f'{where}.tables[{j}]'
                table, created = self.upsert_table(app, tdef, twhere)
                if table is None:
                    broken = True
                else:
                    plans.append((table, tdef, f'{table.ref}', created))
        if broken:
            return  # later checks would only report knock-on errors

        later = []
        for table, tdef, where, created in plans:
            fdefs = self.list_of(tdef, 'fields', where)
            orders = self.field_orders(table, fdefs)
            for k, fdef in enumerate(fdefs):
                fwhere = f'{where}.fields[{k}]'
                existing = table.fields.filter(key=fdef.get('key')).first() if isinstance(fdef, dict) else None
                ftype = (fdef.get('type') if isinstance(fdef, dict) else None) or (existing.type if existing else None)
                if ftype == 'attribute_values':
                    later.append((table, fdef, fwhere, orders))
                else:
                    self.upsert_field(table, fdef, fwhere, orders)
            if created and not fdefs:
                Field.objects.create(table=table, key='name', name='Name', type='text', order=0)
                table.display_field = 'name'
                table.save()
        for table, fdef, where, orders in later:
            self.upsert_field(table, fdef, where, orders)

        for table, tdef, where, created in plans:
            self.table_settings(table, tdef, where, created)
        for table, tdef, where, created in plans:
            self.views(table, tdef, where)
        if self.result.errors:
            return
        self.rows(self.list_of(data, 'rows', 'blueprint'))

    # -- apps and tables -------------------------------------------------------

    def upsert_app(self, adef, where):
        if not isinstance(adef, dict):
            self.error(where, 'each app must be an object.')
            return None
        self.check_keys(adef, {'key', 'name', 'icon', 'description', 'tables'}, where)
        key = self.valid_key(adef, where)
        if key is None:
            return None
        limits = {'name': ('name', 100), 'icon': ('icon', 16), 'description': ('description', 5000)}
        app = App.objects.filter(key=key).first()
        if app is None:
            app = App(key=key, name=key.replace('_', ' ').title(),
                      order=(App.objects.aggregate(m=Max('order'))['m'] or 0) + 1)
            self.update_attrs(app, adef, limits)
            app.save()
            self.change(f'New app "{app.name}"')
        else:
            changed = self.update_attrs(app, adef, limits)
            if changed:
                app.save()
                self.change(f'App "{app.name}": changed {", ".join(changed)}')
        return app

    def upsert_table(self, app, tdef, where):
        if not isinstance(tdef, dict):
            self.error(where, 'each table must be an object.')
            return None, False
        self.check_keys(tdef, {'key', 'name', 'description', 'display_field', 'subtitle_field', 'tree_field',
                               'fields', 'views'}, where)
        key = self.valid_key(tdef, where)
        if key is None:
            return None, False
        limits = {'name': ('name', 100), 'description': ('description', 5000)}
        table = Table.objects.filter(app=app, key=key).first()
        if table is None:
            table = Table(app=app, key=key, name=key.replace('_', ' ').capitalize(), order=app.tables.count())
            self.update_attrs(table, tdef, limits)
            table.save()
            self.change(f'New table "{table.name}" in {app.name}')
            return table, True
        changed = self.update_attrs(table, tdef, limits)
        if changed:
            table.save()
            self.change(f'Table "{table.name}": changed {", ".join(changed)}')
        return table, False

    def table_settings(self, table, tdef, where, created):
        changed = []
        fields = {f.key: f for f in table.fields.all()}
        for attr in ('display_field', 'subtitle_field', 'tree_field'):
            if attr not in tdef:
                continue
            value = str(tdef[attr] or '').strip()
            field = fields.get(value)
            if value and field is None:
                self.error(where, f'{attr} "{value}" is not a field of this table.')
                continue
            if attr == 'tree_field' and field is not None and (
                    field.type != 'link' or field.options.get('target') != table.ref
                    or field.options.get('multiple', True)):
                self.error(where, 'tree_field must be a link to this same table with "multiple": false.')
                continue
            if attr != 'tree_field' and field is not None and (not field.kind.stored or field.type.startswith('attribute')):
                self.error(where, f'{attr} must be a text, number, date or select field.')
                continue
            if getattr(table, attr) != value:
                setattr(table, attr, value)
                changed.append(attr)
        if created and not table.display_field:
            first = next((f for f in fields.values() if f.type in ('text', 'number', 'date', 'select')), None)
            if first is not None:
                table.display_field = first.key
                changed.append('display_field')
        if changed:
            table.save()
            if not created:
                self.change(f'Table "{table.name}": changed {", ".join(changed)}')
            records.refresh_table(table)
            records.refresh_linking_to(table)

    # -- fields ----------------------------------------------------------------

    def field_orders(self, table, fdefs):
        n = table.fields.aggregate(m=Max('order'))['m']
        n = -1 if n is None else n
        orders = {}
        for fdef in fdefs:
            key = fdef.get('key') if isinstance(fdef, dict) else None
            if isinstance(key, str) and key not in orders and not table.fields.filter(key=key).exists():
                n += 1
                orders[key] = n
        return orders

    def upsert_field(self, table, fdef, where, orders):
        if not isinstance(fdef, dict):
            return self.error(where, 'each field must be an object.')
        self.check_keys(fdef, {'key', 'name', 'type', 'required', 'unique', 'help', 'options'}, where)
        key = self.valid_key(fdef, where)
        if key is None:
            return
        where = f'{table.ref}.{key}'
        existing = table.fields.filter(key=key).first()
        ftype = fdef.get('type') or (existing.type if existing else None)
        if ftype not in fieldtypes.REGISTRY:
            return self.error(where, f'unknown type {json.dumps(ftype)}. Use one of: '
                                     f'{", ".join(fieldtypes.REGISTRY)}.')
        kind = fieldtypes.REGISTRY[ftype]
        raw_options = fdef.get('options')
        if raw_options is None:
            raw_options = existing.options if existing and existing.type == ftype else {}
        if not isinstance(raw_options, dict):
            return self.error(where, '"options" must be an object.')
        try:
            options = kind.clean_options(raw_options, table)
        except ValidationError as exc:
            return self.error(where, ' '.join(exc.messages))

        def flag(name, current):
            return bool(fdef[name]) if name in fdef else current

        required = flag('required', existing.required if existing else False) and kind.can_required
        unique = flag('unique', existing.unique if existing else False)
        if unique and not kind.can_unique:
            return self.error(where, f'a {ftype} field can\'t be unique.')
        name = _text(fdef.get('name'), 100) or (existing.name if existing else key.replace('_', ' ').capitalize())
        help_text = _text(fdef['help'], 300) if 'help' in fdef else (existing.help_text if existing else '')

        if existing is None:
            field = Field.objects.create(table=table, key=key, name=name, type=ftype, required=required,
                                         unique=unique, help_text=help_text, options=options,
                                         order=orders.get(key, 0))
            self.change(f'New field "{name}" ({kind.label}) in {table.name}')
            default = kind.default(field) if kind.stored else None
            if default is not None:
                for row in table.rows.all():
                    if key not in row.data:
                        row.data[key] = default
                        row.save(update_fields=['data'])
            return

        changed = []
        if ftype != existing.type:
            if table.rows.exists() and (existing.type, ftype) not in SAFE_TYPE_CHANGES:
                return self.error(where, f'can\'t change the type from {existing.type} to {ftype} because the '
                                         'table has rows. Add a new field instead.')
            changed.append('type')
        if ftype == 'link' and existing.type == 'link' \
                and options.get('target') != existing.options.get('target') and existing.links.exists():
            return self.error(where, 'can\'t change the target of a link that already links rows.')
        if unique and not existing.unique and self.has_duplicates(table, key):
            return self.error(where, f'can\'t make it unique: some rows share the same {name}.')
        for attr, value in (('name', name), ('required', required), ('unique', unique),
                            ('help_text', help_text), ('options', options)):
            if getattr(existing, attr) != value:
                changed.append(attr)
        if changed:
            existing.type, existing.name, existing.required = ftype, name, required
            existing.unique, existing.help_text, existing.options = unique, help_text, options
            existing.save()
            self.change(f'Field "{name}" in {table.name}: changed {", ".join(c.replace("_text", "") for c in changed)}')

    def has_duplicates(self, table, key):
        seen = set()
        for value in table.rows.values_list(f'data__{key}', flat=True):
            if value in (None, ''):
                continue
            norm = value.lower() if isinstance(value, str) else value
            if norm in seen:
                return True
            seen.add(norm)
        return False

    # -- views -----------------------------------------------------------------

    def views(self, table, tdef, where):
        for i, vdef in enumerate(self.list_of(tdef, 'views', where)):
            vwhere = f'{where}.views[{i}]'
            if not isinstance(vdef, dict):
                self.error(vwhere, 'each view must be an object.')
                continue
            self.check_keys(vdef, {'key', 'name', 'columns', 'sort', 'filters', 'page_size'}, vwhere)
            key = self.valid_key(vdef, vwhere)
            if key is None:
                continue
            view = table.views.filter(key=key).first()
            config = dict(view.config) if view else {}
            for name in ('columns', 'sort', 'filters', 'page_size'):
                if name in vdef:
                    config[name] = vdef[name]
            config, errors = schema.clean_view_config(table, config)
            for message in errors:
                self.error(f'{table.ref} view "{key}"', message)
            if errors:
                continue
            name = _text(vdef.get('name'), 100)
            if view is None:
                View.objects.create(table=table, key=key, name=name or key.replace('_', ' ').capitalize(),
                                    config=config, order=table.views.count())
                self.change(f'New view "{name or key}" on {table.name}')
            elif config != view.config or (name and name != view.name):
                view.config, view.name = config, name or view.name
                view.save()
                self.change(f'View "{view.name}" on {table.name}: changed')
        schema.ensure_default_view(table)

    # -- rows ------------------------------------------------------------------

    def rows(self, rdefs):
        refs, pending = {}, []
        for i, rdef in enumerate(rdefs):
            where = f'rows[{i}]'
            if not isinstance(rdef, dict):
                self.error(where, 'each row must be an object.')
                continue
            self.check_keys(rdef, {'table', 'ref', 'values'}, where)
            table = records.resolve_table(rdef.get('table'))
            if table is None:
                self.error(where, f'unknown table {json.dumps(rdef.get("table"))}; use "app_key.table_key".')
                continue
            values = rdef.get('values') or {}
            if not isinstance(values, dict):
                self.error(where, '"values" must be an object.')
                continue
            fields = {f.key: f for f in table.fields.all()}
            unknown = [k for k in values if k not in fields or fields[k].type == 'images']
            if unknown:
                self.error(where, f'{table.ref} has no field {", ".join(unknown)} (images can\'t be imported).')
                continue
            late = {k: v for k, v in values.items() if fields[k].type in ('link', 'attribute_values')}
            early = {k: v for k, v in values.items() if k not in late}
            existing = self.match_row(table, early)
            try:
                row = records.save_row(table, early, row=existing, user=self.user, defer_links=True)
            except records.RowErrors as exc:
                self.error(f'{where} ({table.ref})', str(exc))
                continue
            self.result.row_counts[(table.name, 'updated' if existing else 'new')] += 1
            if rdef.get('ref') not in (None, ''):
                refs[str(rdef['ref'])] = row
            pending.append((table, fields, row, late, where, existing is None))

        for table, fields, row, late, where, is_new in pending:
            resolved = {}
            for key, value in late.items():
                field = fields[key]
                if field.type != 'link':
                    resolved[key] = value
                    continue
                ids = self.resolve_links(field, value, refs, f'{where} ({table.ref}.{key})')
                if ids is not None:
                    resolved[key] = ids
            if is_new:
                for field in fields.values():
                    if field.type == 'link' and field.required and field.key not in late:
                        self.error(where, f'{table.ref}.{field.key} is required.')
            if resolved:
                try:
                    records.save_row(table, resolved, row=row, user=self.user)
                except records.RowErrors as exc:
                    self.error(f'{where} ({table.ref})', str(exc))

    def match_row(self, table, values):
        """An existing row with the same display value, so importing twice updates instead of duplicating."""
        key = table.display_field
        value = values.get(key) if key else None
        if value in (None, ''):
            return None
        lookup = 'iexact' if isinstance(value, str) else 'exact'
        return table.rows.filter(**{f'data__{key}__{lookup}': value}).first()

    def resolve_links(self, field, value, refs, where):
        target = records.resolve_table(field.options.get('target'))
        items = value if isinstance(value, list) else ([] if value in (None, '') else [value])
        ids = []
        for item in items:
            if isinstance(item, str) and item.startswith('@'):
                row = refs.get(item[1:])
                if row is None or row.table_id != target.pk:
                    self.error(where, f'no row with "ref": "{item[1:]}" in {target.ref} earlier in this blueprint.')
                    return None
                ids.append(row.pk)
                continue
            matches = self.rows_by_label(target, str(item))
            if len(matches) != 1:
                problem = 'no row' if not matches else f'{len(matches)} rows'
                self.error(where, f'{problem} in {target.ref} named "{item}". Use "@ref" for rows in this blueprint.')
                return None
            ids.append(matches[0].pk)
        return ids

    def rows_by_label(self, table, text):
        wanted = text.strip().lower()
        labels = records.Labels()
        if table.tree_field and ' > ' in wanted:
            return [r for r in table.rows.all() if labels.label(r, table).lower() == wanted]
        if not table.display_field:
            return []
        return list(table.rows.filter(**{f'data__{table.display_field}__iexact': text.strip()}))


# -- Export --------------------------------------------------------------------

def export(apps=None, include_rows=False):
    """A blueprint of ``apps`` (all when None); importing it changes nothing."""
    apps = list(apps if apps is not None else App.objects.all())
    out = {'osmia_blueprint': VERSION, 'apps': []}
    rows_out = []
    for app in apps:
        adef = {'key': app.key, 'name': app.name}
        if app.icon:
            adef['icon'] = app.icon
        if app.description:
            adef['description'] = app.description
        adef['tables'] = []
        for table in app.tables.all():
            tdef = {'key': table.key, 'name': table.name}
            for attr in ('description', 'display_field', 'subtitle_field', 'tree_field'):
                if getattr(table, attr):
                    tdef[attr] = getattr(table, attr)
            fields = list(table.fields.all())
            tdef['fields'] = [_export_field(f) for f in fields]
            tdef['views'] = [{'key': v.key, 'name': v.name, **v.config} for v in table.views.all()]
            adef['tables'].append(tdef)
            if include_rows:
                rows_out += _export_rows(table, fields)
        out['apps'].append(adef)
    if include_rows and rows_out:
        out['rows'] = rows_out
    return out


def _export_field(field):
    fdef = {'key': field.key, 'name': field.name, 'type': field.type}
    if field.required:
        fdef['required'] = True
    if field.unique:
        fdef['unique'] = True
    if field.help_text:
        fdef['help'] = field.help_text
    if field.options:
        fdef['options'] = field.options
    return fdef


def _export_rows(table, fields):
    from .models import Link
    out = []
    links = {}
    for link in Link.objects.filter(source__table=table).select_related('target__table__app', 'field'):
        links.setdefault((link.source_id, link.field.key), []).append(
            f'@{link.target.table.app.key}_{link.target.table.key}_{link.target_id}')
    for row in table.rows.all():
        values = {}
        for f in fields:
            if f.type == 'link':
                targets = links.get((row.pk, f.key), [])
                if targets:
                    values[f.key] = targets if f.options.get('multiple', True) else targets[0]
            elif f.kind.stored and not f.kind.is_empty(row.data.get(f.key)):
                values[f.key] = row.data[f.key]
        out.append({'table': table.ref, 'ref': f'{table.app.key}_{table.key}_{row.pk}', 'values': values})
    return out
