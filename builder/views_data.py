"""Working with data: app pages, grids, row pages and forms, images."""
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.static import serve

from . import images, records, schema
from .models import App, Attachment, Field, Row, Table
from .views_ai import library


@login_required
def home(request):
    apps = App.objects.annotate(table_count=Count('tables'))
    return render(request, 'builder/home.html', {'apps': apps, 'library': library()})


@login_required
def media(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


@login_required
def app_detail(request, key):
    app = get_object_or_404(App, key=key)
    tables = app.tables.annotate(row_count=Count('rows', distinct=True), field_count=Count('fields', distinct=True))
    return render(request, 'builder/app_detail.html', {'app': app, 'tables': tables})


# -- Grid ----------------------------------------------------------------------

def _filter_text(fields, flt, labels):
    field = fields.get(flt.get('field'))
    if field is None:
        return ''
    op_label = dict(field.kind.filter_ops).get(flt.get('op'), flt.get('op'))
    value = flt.get('value', '')
    if field.type == 'link' and value not in ('', None):
        row = Row.objects.filter(pk=value).select_related('table').first()
        value = labels.label(row) if row else f'#{value}'
    return f'{field.name} {op_label} {value}'.strip()


@login_required
def grid(request, pk, view_id=None):
    table = get_object_or_404(Table.objects.select_related('app'), pk=pk)
    schema.ensure_default_view(table)
    views = list(table.views.all())
    view = next((v for v in views if v.pk == view_id), None) if view_id else views[0]
    if view is None:
        raise Http404('No such view.')
    fields = list(table.fields.all())
    columns = schema.view_columns(view, fields)
    config = view.config or {}
    q = request.GET.get('q', '').strip()
    rows_qs = records.filtered_rows(table, config.get('filters'), q, config.get('sort'))
    page = Paginator(rows_qs, config.get('page_size') or 50).get_page(request.GET.get('page'))
    rows = list(page.object_list)
    targets = records.link_targets(rows, columns)
    labels = records.Labels()
    covers = {}
    image_fields = [f for f in columns if f.type == 'images']
    if image_fields:
        for att in Attachment.objects.filter(row__in=rows, field__in=image_fields).order_by('order', 'id'):
            covers.setdefault((att.row_id, att.field_id), att)
    grid_rows = []
    for row in rows:
        cells = []
        for f in columns:
            value = row.data.get(f.key)
            cell = {'field': f, 'text': f.kind.display(f, value) if f.kind.stored else ''}
            if f.type == 'link':
                cell['links'] = [(labels.label(t, t.table), t.get_absolute_url()) for t in targets.get((row.pk, f.pk), [])]
            elif f.type == 'images':
                cell['image'] = covers.get((row.pk, f.pk))
            elif f.kind.inline_edit:
                cell['editable'] = True
                cell['value_json'] = json.dumps(value)
            cells.append(cell)
        grid_rows.append({'row': row, 'label': labels.label(row, table), 'cells': cells})
    by_key = {f.key: f for f in fields}
    return render(request, 'builder/grid.html', {
        'table': table, 'app': table.app, 'views': views, 'view': view, 'columns': columns,
        'grid_rows': grid_rows, 'page': page, 'q': q,
        'filters': [_filter_text(by_key, flt, labels) for flt in config.get('filters') or []],
        'sorts': [f'{by_key[s["field"]].name} {"↓" if s["dir"] == "desc" else "↑"}'
                  for s in config.get('sort') or [] if s.get('field') in by_key],
    })


# -- Row page ------------------------------------------------------------------

def _related_sections(row, table, labels):
    """Rows of any table that link to this row, under the link's related name.
    In a nested table, rows linked to its sub-rows count too (a category lists
    the parts of its sub-categories), except for the nesting link itself."""
    sections = []
    below = records.descendant_ids(table, row.pk) if table.tree_field else set()
    for field in Field.objects.filter(type='link').select_related('table__app'):
        if field.options.get('target') != table.ref:
            continue
        if field.table_id == table.pk and field.options.get('symmetric'):
            continue
        targets = {row.pk}
        if not (field.table_id == table.pk and field.key == table.tree_field):
            targets |= below
        linked = list(Row.objects.filter(links_out__field=field, links_out__target_id__in=targets)
                      .select_related('table').distinct().order_by('id')[:200])
        heading = field.options.get('related_name') or f'{field.table.name} ({field.name})'
        sections.append({
            'heading': heading, 'field': field,
            'rows': [(labels.label(r, field.table), labels.subtitle(r, field.table), r.get_absolute_url()) for r in linked],
            'add_url': reverse('row_create', args=[field.table_id]) + f'?{field.key}={row.pk}',
        })
    return sections


@login_required
def row_detail(request, row_id):
    row = get_object_or_404(Row.objects.select_related('table__app', 'created_by', 'updated_by'), pk=row_id)
    table = row.table
    fields = list(table.fields.all())
    targets = records.link_targets([row], fields)
    labels = records.Labels()
    items = []
    for f in fields:
        item = {'field': f}
        if f.type == 'link':
            item['links'] = [(labels.label(t, t.table), labels.subtitle(t, t.table), t.get_absolute_url())
                             for t in targets.get((row.pk, f.pk), [])]
        elif f.type == 'images':
            item['images'] = list(row.attachments.filter(field=f))
        elif f.type == 'attribute_values':
            item['pairs'] = list((row.data.get(f.key) or {}).items())
        elif f.type == 'attribute_defs':
            item['defs'] = row.data.get(f.key) or []
        else:
            value = row.data.get(f.key)
            item['text'] = f.kind.display(f, value)
            item['value'] = value
        items.append(item)
    return render(request, 'builder/row_detail.html', {
        'row': row, 'table': table, 'app': table.app, 'label': labels.label(row, table),
        'subtitle': labels.subtitle(row, table), 'items': items,
        'related': _related_sections(row, table, labels),
    })


# -- Row form ------------------------------------------------------------------

def values_from_post(fields, post):
    values = {}
    for f in fields:
        if f.type == 'images':
            continue
        if f.type == 'checkbox':
            values[f.key] = f.key in post
        elif f.type == 'link':
            values[f.key] = [v for v in post.getlist(f.key) if v]
        elif f.type == 'attribute_defs':
            names, hints = post.getlist(f'{f.key}__name'), post.getlist(f'{f.key}__hint')
            required = post.getlist(f'{f.key}__req')
            values[f.key] = [{'name': n, 'hint': h, 'required': r == '1'} for n, h, r in zip(names, hints, required)]
        elif f.type == 'attribute_values':
            prefix = f'{f.key}::'
            values[f.key] = {k[len(prefix):]: post.get(k) for k in post if k.startswith(prefix)}
        else:
            values[f.key] = post.get(f.key, '')
    return values


def _form_widgets(table, fields, row, values, errors):
    """What the form template needs for each field."""
    labels = records.Labels()
    widgets = []
    for f in fields:
        if f.type == 'images':
            continue
        w = {'field': f, 'error': ' '.join(errors.get(f.key, []))}
        value = values.get(f.key) if f.key in values else (row.data.get(f.key) if row else f.kind.default(f))
        if f.type == 'link':
            target = records.resolve_table(f.options.get('target'))
            if f.key in values:
                ids = [int(v) for v in values[f.key] if str(v).isdigit()]
                chosen = {r.pk: r for r in Row.objects.filter(pk__in=ids).select_related('table')}
                picked = [chosen[i] for i in ids if i in chosen]
            elif row:
                picked = [link.target for link in row.links_out.filter(field=f).select_related('target__table')]
            else:
                picked = []
            w['picked'] = [{'id': r.pk, 'label': labels.label(r, r.table)} for r in picked]
            w['search_url'] = reverse('api_search', args=[target.pk]) if target else ''
            w['target'] = target
            w['exclude'] = row.pk if row and target and target.pk == table.pk else ''
        elif f.type == 'attribute_values':
            w['defs_json'] = json.dumps({str(k): v for k, v in records.attribute_defs_map(f).items()})
            w['values_json'] = json.dumps(value or {})
        elif f.type == 'attribute_defs':
            w['defs'] = value or []
        else:
            w['value'] = '' if value is None else value
        widgets.append(w)
    return widgets


@login_required
def row_form(request, pk=None, row_id=None):
    row = get_object_or_404(Row.objects.select_related('table__app'), pk=row_id) if row_id else None
    table = row.table if row else get_object_or_404(Table.objects.select_related('app'), pk=pk)
    fields = list(table.fields.all())
    values, errors = {}, {}
    if request.method == 'POST':
        values = values_from_post(fields, request.POST)
        try:
            saved = records.save_row(table, values, row=row, user=request.user)
        except records.RowErrors as exc:
            errors = exc.errors
            messages.error(request, 'Please fix the marked fields.')
        else:
            messages.success(request, 'Saved.')
            if 'add_another' in request.POST:
                return redirect('row_create', pk=table.pk)
            return redirect(saved.get_absolute_url())
    elif row is None:
        # Pre-fill from the query string, e.g. ?category=12 from a category's page.
        for f in fields:
            if f.key in request.GET:
                values[f.key] = request.GET.getlist(f.key) if f.type == 'link' else request.GET[f.key]
    labels = records.Labels()
    return render(request, 'builder/row_form.html', {
        'table': table, 'app': table.app, 'row': row, 'label': labels.label(row, table) if row else '',
        'widgets': _form_widgets(table, fields, row, values, errors),
        'has_images': any(f.type == 'images' for f in fields),
    })


@login_required
def row_delete(request, row_id):
    row = get_object_or_404(Row.objects.select_related('table__app'), pk=row_id)
    table = row.table
    label = records.Labels().label(row, table)
    if request.method == 'POST':
        linked = list(Row.objects.filter(links_out__target=row).exclude(pk=row.pk).distinct().select_related('table'))
        with transaction.atomic():
            row.delete()
            records.refresh_search(linked)
        messages.success(request, f'Deleted {label}.')
        return redirect(table.get_absolute_url())
    return render(request, 'builder/confirm_delete.html', {
        'title': f'Delete {label}?', 'app': table.app, 'table': table,
        'text': 'This removes the row, its links and its images.', 'cancel_url': row.get_absolute_url(),
    })


# -- Images --------------------------------------------------------------------

@login_required
@require_POST
def images_upload(request, row_id, field_id):
    row = get_object_or_404(Row, pk=row_id)
    field = get_object_or_404(Field, pk=field_id, table_id=row.table_id, type='images')
    order = (row.attachments.filter(field=field).aggregate(m=Max('order'))['m'] or 0) + 1
    added = 0
    for upload in request.FILES.getlist('images'):
        try:
            content = images.process(upload)
        except images.BadImage as exc:
            messages.error(request, str(exc))
            continue
        Attachment.objects.create(row=row, field=field, file=content, order=order, uploaded_by=request.user)
        order += 1
        added += 1
    if added:
        messages.success(request, f'Added {added} image{"s" if added != 1 else ""}.')
    return redirect(row.get_absolute_url())


@login_required
@require_POST
def image_action(request, image_id):
    image = get_object_or_404(Attachment.objects.select_related('row'), pk=image_id)
    action = request.POST.get('action')
    if action == 'delete':
        image.delete()
    elif action == 'cover':
        first = image.row.attachments.filter(field=image.field).order_by('order').first()
        image.order = (first.order - 1) if first else 0
        image.save(update_fields=['order'])
    elif action == 'caption':
        image.caption = request.POST.get('caption', '').strip()[:200]
        image.save(update_fields=['caption'])
    return redirect(image.row.get_absolute_url())


# -- JSON for pickers and grid cells -------------------------------------------

@login_required
def api_search(request, pk):
    table = get_object_or_404(Table.objects.select_related('app'), pk=pk)
    rows = records.filtered_rows(table, q=request.GET.get('q', ''))
    exclude = request.GET.get('exclude', '')
    if exclude.isdigit():
        rows = rows.exclude(pk=int(exclude))
        if table.tree_field:
            rows = rows.exclude(pk__in=records.descendant_ids(table, int(exclude)))
    labels = records.Labels()
    found = [{'id': r.pk, 'label': labels.label(r, table), 'subtitle': labels.subtitle(r, table)}
             for r in rows.select_related('table')[:200]]
    found.sort(key=lambda r: r['label'].lower())
    return JsonResponse({'rows': found[:30]})


@login_required
@require_POST
def api_cell(request, row_id):
    row = get_object_or_404(Row.objects.select_related('table'), pk=row_id)
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponseBadRequest('Expected JSON.')
    field = row.table.fields.filter(key=body.get('field')).first()
    if field is None or not field.kind.inline_edit:
        return JsonResponse({'error': 'This cell can\'t be edited here.'}, status=400)
    try:
        row = records.save_row(row.table, {field.key: body.get('value')}, row=row, user=request.user)
    except records.RowErrors as exc:
        return JsonResponse({'error': ' '.join(exc.errors.get(field.key, [str(exc)]))}, status=400)
    value = row.data.get(field.key)
    return JsonResponse({'text': field.kind.display(field, value), 'value': value})
