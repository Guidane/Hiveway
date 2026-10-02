"""AI round trip and blueprint files.

1. AI builder: the user describes what they want; Osmia makes a prompt from
   AI_GUIDE.md + the current setup (as a blueprint) + the request.
2. The user gives the prompt to any AI (Copilot, ChatGPT, Claude, ...).
3. Import: the user uploads the AI's answer (or pastes it); Osmia shows what
   would change (a dry run) and applies it when the user confirms.
"""
import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from . import blueprint
from .access import staff_required
from .models import App

MAX_IMPORT = 5 * 1024 * 1024


def library():
    """Ready-made blueprints in blueprints/, with whether they are installed."""
    folder = Path(settings.OSMIA_BLUEPRINT_DIR)
    out = []
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        apps = [a for a in data.get('apps', []) if isinstance(a, dict)]
        keys = [a.get('key') for a in apps]
        out.append({
            'name': path.stem, 'title': ', '.join(a.get('name', a.get('key', '')) for a in apps) or path.stem,
            'icon': apps[0].get('icon', '') if apps else '', 'description': data.get('notes', ''),
            'installed': bool(keys) and App.objects.filter(key__in=keys).count() == len(keys),
        })
    return out


def guide_text():
    try:
        return Path(settings.OSMIA_AI_GUIDE).read_text(encoding='utf-8')
    except OSError:
        return ''


def build_prompt(request_text, apps=None):
    current = json.dumps(blueprint.export(apps), indent=1, ensure_ascii=False)
    return (
        f'{guide_text().strip()}\n\n'
        '# The current Osmia setup\n\n'
        'This is everything that exists now, as a blueprint. Only add or change what the request needs; '
        'what you leave out stays as it is.\n\n'
        f'```json\n{current}\n```\n\n'
        '# What the user wants\n\n'
        f'{request_text.strip()}\n\n'
        '# Your answer\n\n'
        'Reply with ONE ```json block holding the blueprint (it may also be given as a file named '
        'something.osmia.json). Put anything the user should know, or anything you could not do with the '
        'blueprint format, in "notes".\n'
    )


@staff_required
def prompt(request):
    apps = list(App.objects.all())
    request_text = request.POST.get('request', '').strip()
    picked = request.POST.getlist('apps') if request.method == 'POST' else [a.key for a in apps]
    prompt_text = ''
    if request.method == 'POST':
        if not request_text:
            messages.error(request, 'Describe what you want first.')
        else:
            prompt_text = build_prompt(request_text, [a for a in apps if a.key in picked])
            if 'download' in request.POST:
                response = HttpResponse(prompt_text, content_type='text/plain; charset=utf-8')
                response['Content-Disposition'] = 'attachment; filename="osmia-prompt.txt"'
                return response
    return render(request, 'builder/ai_prompt.html', {
        'apps': apps, 'picked': picked, 'request_text': request_text, 'prompt_text': prompt_text,
    })


def _read_upload(request):
    upload = request.FILES.get('file')
    if upload is None:
        return request.POST.get('text', '')
    if upload.size > MAX_IMPORT:
        raise ValueError('The file is larger than 5 MB.')
    raw = upload.read()
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('The file is not a text file.')


def _preview(request, text, source=''):
    """Parse ``text`` and dry-run it; the page shows the changes and an Apply button."""
    context = {'text': text, 'source': source}
    try:
        data = blueprint.parse_text(text)
    except ValueError as exc:
        context['parse_error'] = str(exc)
        return render(request, 'builder/ai_import.html', context)
    result = blueprint.apply(data, user=request.user, dry_run=True)
    context.update(result=result, blueprint_json=json.dumps(data, indent=1, ensure_ascii=False))
    return render(request, 'builder/ai_import.html', context)


@staff_required
def import_view(request):
    if request.method != 'POST':
        return render(request, 'builder/ai_import.html', {})
    if request.POST.get('action') == 'apply':
        try:
            data = blueprint.parse_text(request.POST.get('blueprint_json', ''))
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, 'builder/ai_import.html', {})
        result = blueprint.apply(data, user=request.user)
        if not result.ok:
            # Something changed since the preview; show the problems.
            return render(request, 'builder/ai_import.html', {
                'result': result, 'blueprint_json': json.dumps(data, indent=1, ensure_ascii=False),
            })
        messages.success(request, f'Applied: {len(result.summary())} change{"s" if len(result.summary()) != 1 else ""}.')
        if result.notes:
            messages.info(request, f'Notes: {result.notes}')
        first = next((a.get('key') for a in data.get('apps', []) if isinstance(a, dict)), None)
        app = App.objects.filter(key=first).first() if first else None
        return redirect(app.get_absolute_url() if app else 'home')
    try:
        text = _read_upload(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return render(request, 'builder/ai_import.html', {})
    upload = request.FILES.get('file')
    return _preview(request, text, source=upload.name if upload else '')


@staff_required
def library_install(request, name):
    path = Path(settings.OSMIA_BLUEPRINT_DIR) / f'{name}.json'
    if not path.is_file() or path.parent.resolve() != Path(settings.OSMIA_BLUEPRINT_DIR).resolve():
        raise Http404('No such blueprint.')
    return _preview(request, path.read_text(encoding='utf-8'), source=path.name)


@staff_required
def export(request, key=None):
    apps = [get_object_or_404(App, key=key)] if key else None
    data = blueprint.export(apps, include_rows=request.GET.get('rows') == '1')
    name = slugify(key or 'osmia') + ('-with-data' if request.GET.get('rows') == '1' else '') + '.osmia.json'
    response = HttpResponse(json.dumps(data, indent=1, ensure_ascii=False), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    return response
