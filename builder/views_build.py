"""Building apps in the browser (staff only).

Creating and changing apps, tables, fields and views is done by sending a
small blueprint to blueprint.apply, the same way AI answers are applied, so
there is one set of rules. Deleting is done here (blueprints never delete).
"""
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import blueprint, fieldtypes, records, schema
from .access import staff_required
from .models import App, Field, Table, View


def _apply(request, apps):
    result = blueprint.apply({'osmia_blueprint': blueprint.VERSION, 'apps': apps}, user=request.user)
    for error in result.errors:
        messages.error(request, error)
    return result.ok


def _app_bp(app, **table):
    return [{'key': app.key, 'tables': [table]}] if table else [{'key': app.key}]


# -- Apps ----------------------------------------------------------------------

@staff_required
def app_form(request, key=None):
    app = get_object_or_404(App, key=key) if key else None
    data = request.POST if request.method == 'POST' else {
        'name': app.name if app else '', 'icon': app.icon if app else '', 'description': app.description if app else '',
    }
    if request.method == 'POST':
        name = data.get('name', '').strip()
        if not name:
            messages.error(request, 'Give the app a name.')
        else:
            new_key = app.key if app else schema.make_key(name, App.objects.values_list('key', flat=True), 'app')
            spec = {'key': new_key, 'name': name, 'icon': data.get('icon', '').strip(),
                    'description': data.get('description', '')}
            if _apply(request, [spec]):
                messages.success(request, 'App saved.')
                return redirect('app_detail', key=new_key)
    return render(request, 'builder/app_form.html', {'app': app, 'data': data})


@staff_required
def app_delete(request, key):
    app = get_object_or_404(App, key=key)
    if request.method == 'POST':
        error = schema.delete_app(app)
        if error:
            messages.error(request, error)
            return redirect('app_detail', key=key)
        messages.success(request, f'Deleted {app.name}.')
        return redirect('home')
    rows = sum(t.rows.count() for t in app.tables.all())
    return render(request, 'builder/confirm_delete.html', {
        'title': f'Delete the app {app.name}?', 'app': app, 'cancel_url': app.get_absolute_url(),
        'text': f'This deletes its {app.tables.count()} tables and {rows} rows, for good.',
    })


# -- Tables --------------------------------------------------------------------

@staff_required
def table_create(request, key):
    app = get_object_or_404(App, key=key)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Give the table a name.')
        else:
            table_key = schema.make_key(name, app.tables.values_list('key', flat=True), 'table')
            spec = {'key': table_key, 'name': name, 'description': request.POST.get('description', '')}
            if _apply(request, _app_bp(app, **spec)):
                table = app.tables.get(key=table_key)
                messages.success(request, f'Table {name} created with a Name field. Add more fields with + Field.')
                return redirect(table.get_absolute_url())
    return render(request, 'builder/table_create.html', {'app': app, 'data': request.POST})


@staff_required
def table_settings(request, pk):
    table = get_object_or_404(Table.objects.select_related('app'), pk=pk)
    fields = list(table.fields.all())
    if request.method == 'POST':
        spec = {'key': table.key, 'name': request.POST.get('name', ''), 'description': request.POST.get('description', ''),
                'display_field': request.POST.get('display_field', ''),
                'subtitle_field': request.POST.get('subtitle_field', ''),
                'tree_field': request.POST.get('tree_field', '')}
        if _apply(request, _app_bp(table.app, **spec)):
            messages.success(request, 'Table saved.')
            return redirect('table_settings', pk=pk)
    return render(request, 'builder/table_settings.html', {
        'table': table, 'app': table.app, 'fields': fields,
        'label_fields': [f for f in fields if f.kind.stored and not f.type.startswith('attribute')],
        'tree_fields': [f for f in fields if f.type == 'link' and f.options.get('target') == table.ref
                        and not f.options.get('multiple', True)],
    })


@staff_required
def table_delete(request, pk):
    table = get_object_or_404(Table.objects.select_related('app'), pk=pk)
    if request.method == 'POST':
        error = schema.delete_table(table)
        if error:
            messages.error(request, error)
            return redirect('table_settings', pk=pk)
        messages.success(request, f'Deleted {table.name}.')
        return redirect('app_detail', key=table.app.key)
    return render(request, 'builder/confirm_delete.html', {
        'title': f'Delete the table {table.name}?', 'app': table.app, 'table': table,
        'cancel_url': reverse('table_settings', args=[pk]),
        'text': f'This deletes its {table.rows.count()} rows, its fields and views, for good.',
    })


# -- Fields --------------------------------------------------------------------

def options_from_post(ftype, post):
    def get(name):
        return post.get(f'opt_{ftype}_{name}', '').strip()

    if ftype in ('text', 'long_text', 'url', 'email'):
        return {'default': get('default')} if get('default') else {}
    if ftype == 'number':
        return {k: get(k) for k in ('decimals', 'default') if get(k)}
    if ftype == 'checkbox':
        return {'default': f'opt_{ftype}_default' in post}
    if ftype == 'select':
        return {'choices': get('choices').splitlines(), 'default': get('default')}
    if ftype == 'link':
        return {'target': get('target'), 'multiple': f'opt_{ftype}_multiple' in post,
                'symmetric': f'opt_{ftype}_symmetric' in post, 'related_name': get('related_name')}
    if ftype == 'attribute_values':
        return {'via': get('via'), 'defs': get('defs')}
    return {}


@staff_required
def field_form(request, pk=None, field_id=None):
    field = get_object_or_404(Field.objects.select_related('table__app'), pk=field_id) if field_id else None
    table = field.table if field else get_object_or_404(Table.objects.select_related('app'), pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        ftype = request.POST.get('type') or (field.type if field else 'text')
        if not name:
            messages.error(request, 'Give the field a name.')
        else:
            key = field.key if field else schema.make_key(name, table.fields.values_list('key', flat=True), 'field')
            spec = {'key': key, 'name': name, 'type': ftype, 'required': 'required' in request.POST,
                    'unique': 'unique' in request.POST, 'help': request.POST.get('help', ''),
                    'options': options_from_post(ftype, request.POST)}
            if _apply(request, _app_bp(table.app, key=table.key, fields=[spec])):
                messages.success(request, f'Field {name} saved.')
                return redirect(request.POST.get('next') or table.get_absolute_url())
        current = {'name': name, 'type': ftype, 'help': request.POST.get('help', ''),
                   'required': 'required' in request.POST, 'unique': 'unique' in request.POST,
                   'options': options_from_post(ftype, request.POST)}
    elif field:
        current = {'name': field.name, 'type': field.type, 'help': field.help_text, 'required': field.required,
                   'unique': field.unique, 'options': field.options}
    else:
        current = {'name': '', 'type': request.GET.get('type', 'text'), 'help': '', 'required': False,
                   'unique': False, 'options': {}}
    opts = current['options'] or {}
    if isinstance(opts.get('choices'), list):
        opts = {**opts, 'choices_text': '\n'.join(opts['choices'])}
    defs_fields = [f for f in Field.objects.filter(type='attribute_defs').select_related('table__app')]
    return render(request, 'builder/field_form.html', {
        'table': table, 'app': table.app, 'field': field, 'current': current, 'opts': opts,
        'types': [(k, t) for k, t in fieldtypes.REGISTRY.items()],
        'tables': Table.objects.select_related('app').order_by('app__order', 'app__name', 'order'),
        'via_fields': [f for f in table.fields.filter(type='link') if not f.options.get('multiple', True)],
        'defs_keys': sorted({f.key for f in defs_fields}),
        'defs_fields': defs_fields,
        'next': request.GET.get('next') or request.POST.get('next', ''),
    })


@staff_required
def field_delete(request, field_id):
    field = get_object_or_404(Field.objects.select_related('table__app'), pk=field_id)
    table = field.table
    if request.method == 'POST':
        error = schema.delete_field(field)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f'Deleted the field {field.name}.')
        return redirect('table_settings', pk=table.pk)
    return render(request, 'builder/confirm_delete.html', {
        'title': f'Delete the field {field.name}?', 'app': table.app, 'table': table,
        'cancel_url': reverse('table_settings', args=[table.pk]),
        'text': f'Its values in all {table.rows.count()} rows of {table.name} are deleted, for good.',
    })


@staff_required
@require_POST
def field_move(request, field_id):
    field = get_object_or_404(Field, pk=field_id)
    schema.move_field(field, -1 if request.POST.get('dir') == 'up' else 1)
    return redirect('table_settings', pk=field.table_id)


# -- Views ---------------------------------------------------------------------

@staff_required
def view_form(request, pk=None, view_id=None):
    view = get_object_or_404(View.objects.select_related('table__app'), pk=view_id) if view_id else None
    table = view.table if view else get_object_or_404(Table.objects.select_related('app'), pk=pk)
    fields = list(table.fields.all())
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        columns = request.POST.getlist('columns')
        if columns == [f.key for f in fields]:
            columns = []  # everything, in table order: new fields show up too
        sort = [{'field': f, 'dir': d} for f, d in zip(request.POST.getlist('sort_field'), request.POST.getlist('sort_dir')) if f]
        filters = []
        for f, op, value in zip(request.POST.getlist('filter_field'), request.POST.getlist('filter_op'),
                                request.POST.getlist('filter_value')):
            if f and op:
                filters.append({'field': f, 'op': op, 'value': value})
        key = view.key if view else schema.make_key(name or 'view', table.views.values_list('key', flat=True), 'view')
        spec = {'key': key, 'name': name or 'View', 'columns': columns, 'sort': sort, 'filters': filters,
                'page_size': request.POST.get('page_size') or 50}
        if _apply(request, _app_bp(table.app, key=table.key, views=[spec])):
            messages.success(request, 'View saved.')
            return redirect(table.views.get(key=key).get_absolute_url())
    config = view.config if view else {}
    shown = schema.view_columns(view, fields) if view else fields
    ordered = shown + [f for f in fields if f not in shown]
    labels = records.Labels()
    link_choices = {}
    for f in fields:
        if f.type == 'link':
            target = records.resolve_table(f.options.get('target'))
            if target:
                rows = list(target.rows.all()[:500])
                link_choices[f.key] = sorted(([r.pk, labels.label(r, target)] for r in rows), key=lambda x: x[1].lower())
    return render(request, 'builder/view_form.html', {
        'table': table, 'app': table.app, 'view': view, 'config': config,
        'columns': [(f, f in shown) for f in ordered],
        'fields': fields,
        'sorts': (config.get('sort') or []) + [{}],
        'filters': (config.get('filters') or []) + [{}],
        'field_meta': json.dumps({f.key: {'type': f.type, 'ops': f.kind.filter_ops,
                                          'choices': f.options.get('choices', [])} for f in fields}),
        'link_choices': json.dumps(link_choices),
    })


@staff_required
@require_POST
def view_delete(request, view_id):
    view = get_object_or_404(View.objects.select_related('table'), pk=view_id)
    table = view.table
    if table.views.count() <= 1:
        messages.error(request, 'A table keeps at least one view.')
        return redirect(view.get_absolute_url())
    view.delete()
    messages.success(request, f'Deleted the view {view.name}.')
    return redirect(table.get_absolute_url())
