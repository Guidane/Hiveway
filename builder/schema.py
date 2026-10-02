"""Schema helpers shared by the builder pages and blueprints: keys, view
settings, and deleting things (blueprints never delete)."""
import re

from django.db import transaction

from . import records
from .models import Field, Link, View


def make_key(name, taken=(), fallback='item'):
    """A key from a name ("Part number" -> "part_number"), unique among ``taken``."""
    key = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')[:40]
    if not key or not key[0].isalpha():
        key = f'{fallback}_{key}'.strip('_')
    taken = set(taken)
    candidate, n = key, 2
    while candidate in taken:
        candidate = f'{key}_{n}'
        n += 1
    return candidate


def clean_view_config(table, config):
    """Normalise a view's settings; returns (config, errors)."""
    fields = {f.key: f for f in table.fields.all()}
    errors, out = [], {}
    columns = config.get('columns')
    if columns is not None:
        if not isinstance(columns, list):
            errors.append('columns must be a list of field keys.')
        else:
            unknown = [c for c in columns if c not in fields]
            if unknown:
                errors.append(f'Unknown columns: {", ".join(map(str, unknown))}.')
            out['columns'] = [c for c in dict.fromkeys(columns) if c in fields]
    sort = []
    for item in config.get('sort') or []:
        field = fields.get(item.get('field')) if isinstance(item, dict) else None
        if field is None:
            errors.append(f'Unknown sort field: {item}.')
        elif not (field.kind.sortable and field.kind.stored):
            errors.append(f'Rows can\'t be sorted by {field.key} ({field.type}).')
        else:
            sort.append({'field': field.key, 'dir': 'desc' if item.get('dir') == 'desc' else 'asc'})
    if sort:
        out['sort'] = sort
    filters = []
    for item in config.get('filters') or []:
        field = fields.get(item.get('field')) if isinstance(item, dict) else None
        if field is None:
            errors.append(f'Unknown filter field: {item}.')
            continue
        ops = [op for op, _ in field.kind.filter_ops]
        if item.get('op') not in ops:
            errors.append(f'Filter on {field.key}: op must be one of {", ".join(ops) or "(none)"}.')
            continue
        entry = {'field': field.key, 'op': item['op']}
        if item['op'] not in ('empty', 'not_empty', 'true', 'false'):
            value = item.get('value')
            if field.type == 'link':
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    errors.append(f'Filter on {field.key}: value must be a row id.')
                    continue
            entry['value'] = value if isinstance(value, (int, float)) else str(value or '')
        filters.append(entry)
    if filters:
        out['filters'] = filters
    if config.get('page_size') not in (None, ''):
        try:
            out['page_size'] = max(10, min(500, int(config['page_size'])))
        except (TypeError, ValueError):
            errors.append('page_size must be a number.')
    return out, errors


def view_columns(view, fields):
    """The fields a view shows, in its order (all fields when it names none)."""
    by_key = {f.key: f for f in fields}
    keys = (view.config or {}).get('columns') if view else None
    if not keys:
        return list(fields)
    return [by_key[k] for k in keys if k in by_key]


def ensure_default_view(table):
    if not table.views.exists():
        View.objects.create(table=table, key='all', name=f'All {table.name.lower()}', config={})


# -- Deleting ------------------------------------------------------------------

def links_into(table, exclude_app=None):
    """Link fields of other tables that point at ``table``."""
    out = []
    for field in Field.objects.filter(type='link').exclude(table=table).select_related('table__app'):
        if field.options.get('target') == table.ref and field.table.app != exclude_app:
            out.append(field)
    return out


def field_users(field):
    """Attribute-value fields that need ``field`` (as their via link or defs list)."""
    users = []
    for other in Field.objects.filter(type='attribute_values').select_related('table__app'):
        via_table = other.table
        if other.table_id == field.table_id and other.options.get('via') == field.key:
            users.append(other)
            continue
        via = Field.objects.filter(table=via_table, key=other.options.get('via')).first()
        if via and via.options.get('target') == field.table.ref and other.options.get('defs') == field.key:
            users.append(other)
    return users


def delete_field(field):
    """Delete a field and its values; returns an error message or None."""
    users = field_users(field)
    if users:
        names = ', '.join(f'{u.table.name} › {u.name}' for u in users)
        return f'{field.name} is used by {names}. Change or delete that first.'
    table = field.table
    with transaction.atomic():
        for row in table.rows.all():
            if field.key in row.data:
                row.data.pop(field.key)
                row.save(update_fields=['data'])
        for attr in ('display_field', 'subtitle_field', 'tree_field'):
            if getattr(table, attr) == field.key:
                setattr(table, attr, '')
        table.save()
        for view in table.views.all():
            config, _ = clean_view_config_without(table, view.config, field.key)
            view.config = config
            view.save()
        field.delete()
        records.refresh_table(table)
        records.refresh_linking_to(table)
    return None


def clean_view_config_without(table, config, key):
    config = dict(config or {})
    if 'columns' in config:
        config['columns'] = [c for c in config['columns'] if c != key]
    for name in ('sort', 'filters'):
        if name in config:
            config[name] = [i for i in config[name] if i.get('field') != key]
    return config, []


def delete_table(table):
    """Returns an error message or None."""
    blockers = links_into(table)
    if blockers:
        names = ', '.join(f'{f.table.app.name} › {f.table.name} › {f.name}' for f in blockers)
        return f'{table.name} is linked from {names}. Delete those links first.'
    with transaction.atomic():
        Link.objects.filter(target__table=table).delete()
        table.delete()
    return None


def delete_app(app):
    for table in app.tables.all():
        blockers = links_into(table, exclude_app=app)
        if blockers:
            names = ', '.join(f'{f.table.app.name} › {f.table.name} › {f.name}' for f in blockers)
            return f'{table.name} is linked from {names}. Delete those links first.'
    with transaction.atomic():
        Link.objects.filter(target__table__app=app).delete()
        app.delete()
    return None


def move_field(field, step):
    fields = list(field.table.fields.all())
    index = fields.index(field)
    other = index + step
    if 0 <= other < len(fields):
        fields[index], fields[other] = fields[other], fields[index]
        for n, f in enumerate(fields):
            if f.order != n:
                Field.objects.filter(pk=f.pk).update(order=n)
