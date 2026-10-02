"""Reading and writing rows: validation, links, labels, search and filters.

Everything that changes rows goes through save_row, whether it comes from a
form, a grid cell or a blueprint, so the rules are the same everywhere.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, FloatField, OuterRef, Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Lower

from .models import Field, Link, Row, Table


class RowErrors(Exception):
    """Validation failed; ``errors`` maps field key -> list of messages."""

    def __init__(self, errors):
        super().__init__('; '.join(f'{k}: {" ".join(v)}' for k, v in errors.items()))
        self.errors = errors


def resolve_table(ref):
    """The table for "app.table", or None."""
    app_key, _, table_key = str(ref or '').partition('.')
    return Table.objects.select_related('app').filter(app__key=app_key, key=table_key).first()


def plain(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ', '.join(plain(v.get('name') if isinstance(v, dict) else v) for v in value)
    if isinstance(value, dict):
        return '; '.join(f'{k}: {v}' for k, v in value.items())
    return str(value)


# -- Trees (rows nested through a link to their own table) ---------------------

def tree_field(table):
    if not table.tree_field:
        return None
    return Field.objects.filter(table=table, key=table.tree_field, type='link').first()


def parent_map(table):
    """{row id: parent row id} for a nested table."""
    field = tree_field(table)
    if field is None:
        return {}
    return dict(Link.objects.filter(field=field).values_list('source_id', 'target_id'))


def descendant_ids(table, row_id, parents=None):
    parents = parent_map(table) if parents is None else parents
    children = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    found, stack = set(), [row_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def ancestor_ids(table, row_id, parents=None):
    """Parent first, then its parent, ..."""
    parents = parent_map(table) if parents is None else parents
    out, current = [], parents.get(row_id)
    while current and current not in out and current != row_id:
        out.append(current)
        current = parents.get(current)
    return out


# -- Labels --------------------------------------------------------------------

class Labels:
    """Names of rows for links and lists ("Hardware > Fasteners" in a nested
    table). Caches what it reads, so make a new one after saving."""

    def __init__(self):
        self._trees = {}

    def _tree(self, table):
        if table.pk not in self._trees:
            rows = {r.pk: r for r in Row.objects.filter(table=table).only('id', 'data', 'table_id')}
            self._trees[table.pk] = (parent_map(table), rows)
        return self._trees[table.pk]

    def name(self, row, table):
        value = row.data.get(table.display_field) if table.display_field else None
        text = plain(value)
        return text if text else f'#{row.pk}'

    def label(self, row, table=None):
        table = table or row.table
        if not table.tree_field:
            return self.name(row, table)
        parents, rows = self._tree(table)
        names = [self.name(row, table)]
        names += [self.name(rows[pk], table) for pk in ancestor_ids(table, row.pk, parents) if pk in rows]
        return ' > '.join(reversed(names))

    def subtitle(self, row, table=None):
        table = table or row.table
        if not table.subtitle_field or table.subtitle_field == table.display_field:
            return ''
        return plain(row.data.get(table.subtitle_field))


# -- Attributes ----------------------------------------------------------------

def attribute_defs_for(field, target_id):
    """The attributes a row linked through ``field``'s via link should have:
    those listed on the linked row, then those of its parents."""
    via = Field.objects.filter(table_id=field.table_id, key=field.options.get('via')).first()
    target = resolve_table(via.options.get('target')) if via else None
    if target is None or not target_id:
        return []
    defs_key = field.options.get('defs')
    chain = [target_id, *ancestor_ids(target, target_id)] if target.tree_field else [target_id]
    rows = {r.pk: r for r in Row.objects.filter(table=target, pk__in=chain)}
    out, seen = [], set()
    for pk in chain:
        for item in (rows[pk].data.get(defs_key) or []) if pk in rows else []:
            if item.get('name', '').lower() not in seen:
                seen.add(item['name'].lower())
                out.append(item)
    return out


def attribute_defs_map(field):
    """{linked row id: its attributes} for every row the via link can pick,
    so the form can show the right inputs as soon as one is picked."""
    via = Field.objects.filter(table_id=field.table_id, key=field.options.get('via')).first()
    target = resolve_table(via.options.get('target')) if via else None
    if target is None:
        return {}
    defs_key = field.options.get('defs')
    parents = parent_map(target)
    own = {r.pk: r.data.get(defs_key) or [] for r in Row.objects.filter(table=target).only('id', 'data')}
    out = {}
    for pk in own:
        merged, seen = [], set()
        for step in [pk, *ancestor_ids(target, pk, parents)]:
            for item in own.get(step, []):
                if item.get('name', '').lower() not in seen:
                    seen.add(item['name'].lower())
                    merged.append(item)
        out[pk] = merged
    return out


# -- Saving --------------------------------------------------------------------

def save_row(table, values, row=None, user=None, defer_links=False):
    """Check ``values`` (field key -> value from a form or JSON) and save them.

    ``row`` None makes a new row. Keys left out keep their value (new rows get
    the field's default). Link values are lists of row ids. With
    ``defer_links``, missing required links are not reported yet (blueprints
    set links in a second step). Raises RowErrors.
    """
    fields = list(table.fields.all())
    is_new = row is None
    data = {} if is_new else dict(row.data)
    errors, links = {}, {}

    for f in fields:
        kind = f.kind
        if f.type in ('images', 'attribute_values'):
            continue
        if f.key not in values:
            if is_new and f.type == 'link':
                if f.required and not defer_links:
                    errors[f.key] = ['This field is required.']
            elif is_new:
                data[f.key] = kind.default(f)
                if f.required and kind.is_empty(data[f.key]):
                    errors[f.key] = ['This field is required.']
            continue
        try:
            value = kind.clean(f, values[f.key])
        except ValidationError as exc:
            errors[f.key] = list(exc.messages)
            continue
        if f.required and kind.can_required and kind.is_empty(value):
            errors[f.key] = ['This field is required.']
            continue
        if f.type == 'link':
            links[f] = value
        else:
            data[f.key] = value

    for f, ids in links.items():
        target = resolve_table(f.options.get('target'))
        found = set(Row.objects.filter(table=target, pk__in=ids).values_list('pk', flat=True)) if target else set()
        missing = [str(pk) for pk in ids if pk not in found]
        if missing:
            errors[f.key] = [f'No row {", ".join(missing)} in {target.name if target else "the linked table"}.']
        elif row is not None and row.pk in ids:
            errors[f.key] = ["A row can't link to itself."]
        elif row is not None and f.key == table.tree_field and ids and ids[0] in descendant_ids(table, row.pk):
            errors[f.key] = ["That would put it inside itself."]

    for f in fields:
        value = data.get(f.key)
        if f.unique and f.key in values and f.key not in errors and not f.kind.is_empty(value):
            lookup = 'iexact' if isinstance(value, str) else 'exact'
            clash = table.rows.filter(**{f'data__{f.key}__{lookup}': value})
            if row is not None:
                clash = clash.exclude(pk=row.pk)
            if clash.exists():
                errors[f.key] = [f'There is already a row with {f.name} "{f.kind.display(f, value)}".']

    for f in fields:
        if f.type != 'attribute_values':
            continue
        via = next((x for x in fields if x.key == f.options.get('via')), None)
        if f.key not in values and via not in links:
            if is_new:
                data[f.key] = {}
            continue
        current = data.get(f.key) or {}
        if f.key in values:
            try:
                current = f.kind.clean(f, values[f.key])
            except ValidationError as exc:
                errors[f.key] = list(exc.messages)
                continue
        if via in links:
            target_ids = links[via]
        elif row is not None and via is not None:
            target_ids = list(Link.objects.filter(field=via, source=row).values_list('target_id', flat=True))
        else:
            target_ids = []
        defs = attribute_defs_for(f, target_ids[0]) if target_ids and via and via.key not in errors else []
        by_lower = {d['name'].lower(): d['name'] for d in defs}
        kept = {by_lower[k.lower()]: v for k, v in current.items() if k.lower() in by_lower}
        missing = [d['name'] for d in defs if d.get('required') and not kept.get(d['name'])]
        if missing and f.key in values:
            errors[f.key] = [f'Required: {", ".join(missing)}.']
        data[f.key] = kept

    if errors:
        raise RowErrors(errors)

    with transaction.atomic():
        if is_new:
            row = Row(table=table, created_by=user)
        row.data = data
        row.updated_by = user
        row.save()
        touched = set()
        for f, ids in links.items():
            touched |= set_links(f, row, ids)
        refresh_around(row, table, touched)
    return row


def set_links(field, row, ids):
    """Make ``row`` link to exactly ``ids`` through ``field``; returns the ids
    of rows whose links changed on the other side (two-way fields)."""
    current = set(Link.objects.filter(field=field, source=row).values_list('target_id', flat=True))
    removed = current - set(ids)
    Link.objects.filter(field=field, source=row, target_id__in=removed).delete()
    for order, pk in enumerate(ids):
        Link.objects.update_or_create(field=field, source=row, target_id=pk, defaults={'order': order})
    if not field.options.get('symmetric'):
        return set()
    Link.objects.filter(field=field, target=row, source_id__in=removed).delete()
    for pk in ids:
        if not Link.objects.filter(field=field, source_id=pk, target=row).exists():
            Link.objects.create(field=field, source_id=pk, target=row, order=10_000)
    return removed | set(ids)


def refresh_around(row, table, extra_ids=()):
    """Update the search text of ``row`` and of the rows that show its label."""
    affected = {row.pk}
    if table.tree_field:
        affected |= descendant_ids(table, row.pk)
    ids = set(Row.objects.filter(links_out__target_id__in=affected).values_list('pk', flat=True))
    ids |= affected | set(extra_ids)
    refresh_search(Row.objects.filter(pk__in=ids).select_related('table'))


def refresh_search(rows):
    labels = Labels()
    fields_by_table = {}
    for row in rows:
        fields = fields_by_table.get(row.table_id)
        if fields is None:
            fields = fields_by_table[row.table_id] = list(Field.objects.filter(table_id=row.table_id))
        parts = []
        for f in fields:
            value = row.data.get(f.key)
            if f.kind.stored and not f.kind.is_empty(value):
                parts.append(f.kind.search_text(f, value))
        for link in Link.objects.filter(source=row).select_related('target__table'):
            parts.append(labels.label(link.target, link.target.table))
        text = ' '.join(p for p in parts if p).lower()[:20000]
        if text != row.search_text:
            Row.objects.filter(pk=row.pk).update(search_text=text)
            row.search_text = text


def refresh_table(table):
    refresh_search(Row.objects.filter(table=table).select_related('table'))


def refresh_linking_to(table):
    """Rows that show labels of ``table``'s rows (after its labels changed)."""
    refresh_search(Row.objects.filter(links_out__target__table=table).distinct().select_related('table'))


# -- Querying ------------------------------------------------------------------

def filtered_rows(table, filters=(), q='', sort=()):
    fields = {f.key: f for f in table.fields.all()}
    qs = table.rows.all()
    for flt in filters or ():
        field = fields.get(flt.get('field'))
        if field is not None:
            qs = apply_filter(qs, field, flt.get('op'), flt.get('value'))
    for word in (q or '').lower().split():
        qs = qs.filter(search_text__contains=word)
    order = []
    for item in sort or ():
        field = fields.get(item.get('field'))
        if field is None or not field.kind.sortable or not field.kind.stored:
            continue
        expr = KeyTextTransform(field.key, 'data')
        if field.type == 'number':
            expr = Cast(expr, FloatField())
        elif field.type in ('text', 'long_text', 'select', 'url', 'email'):
            expr = Lower(expr)
        order.append(expr.desc(nulls_last=True) if item.get('dir') == 'desc' else expr.asc(nulls_last=True))
    return qs.order_by(*order, 'id')


def _empty_q(key):
    return Q(**{f'data__{key}__isnull': True}) | Q(**{f'data__{key}': None}) | Q(**{f'data__{key}': ''})


def apply_filter(qs, field, op, value):
    key = field.key
    if field.type == 'link':
        has_link = Exists(Link.objects.filter(field=field, source=OuterRef('pk')))
        if op == 'empty':
            return qs.filter(~has_link)
        if op == 'not_empty':
            return qs.filter(has_link)
        try:
            target_id = int(value)
        except (TypeError, ValueError):
            return qs.none()
        ids = {target_id}
        target = resolve_table(field.options.get('target'))
        if target is not None and target.tree_field:
            ids |= descendant_ids(target, target_id)
        return qs.filter(Exists(Link.objects.filter(field=field, source=OuterRef('pk'), target_id__in=ids)))
    if field.type == 'checkbox':
        ticked = Q(**{f'data__{key}': True})
        return qs.filter(ticked) if op == 'true' else qs.exclude(ticked)
    if op == 'empty':
        return qs.filter(_empty_q(key))
    if op == 'not_empty':
        return qs.exclude(_empty_q(key))
    if field.type == 'number':
        try:
            number = field.kind.clean(field, value)
        except ValidationError:
            return qs.none()
        if number is None:
            return qs
        lookups = {'eq': 'exact', 'lt': 'lt', 'lte': 'lte', 'gt': 'gt', 'gte': 'gte'}
        if op == 'ne':
            return qs.exclude(**{f'data__{key}': number})
        return qs.filter(**{f'data__{key}__{lookups.get(op, "exact")}': number})
    if field.type == 'date':
        lookups = {'on': 'exact', 'before': 'lt', 'after': 'gt'}
        return qs.filter(**{f'data__{key}__{lookups.get(op, "exact")}': str(value or '')[:10]})
    text = str(value or '')
    if op == 'contains':
        return qs.filter(**{f'data__{key}__icontains': text})
    if op == 'equals':
        return qs.filter(**{f'data__{key}__iexact': text})
    if op == 'not_equals':
        return qs.exclude(**{f'data__{key}__iexact': text})
    return qs


def link_targets(rows, fields):
    """{(row id, field id): [target rows]} for showing link cells."""
    link_fields = [f for f in fields if f.type == 'link']
    out = {}
    if not link_fields or not rows:
        return out
    links = (Link.objects.filter(source__in=rows, field__in=link_fields)
             .select_related('target__table__app').order_by('order', 'id'))
    for link in links:
        out.setdefault((link.source_id, link.field_id), []).append(link.target)
    return out
