import json

from django.core.management.base import BaseCommand, CommandError

from builder import blueprint
from builder.models import App


class Command(BaseCommand):
    help = 'Print a blueprint of the apps (all, or --app KEY), optionally with their rows.'

    def add_arguments(self, parser):
        parser.add_argument('--app', action='append', default=[], help='App key; repeat for several.')
        parser.add_argument('--rows', action='store_true', help='Include the rows.')

    def handle(self, app=(), rows=False, **options):
        apps = None
        if app:
            apps = list(App.objects.filter(key__in=app))
            missing = set(app) - {a.key for a in apps}
            if missing:
                raise CommandError(f'No app {", ".join(sorted(missing))}.')
        data = blueprint.export(apps, include_rows=rows)
        self.stdout.write(json.dumps(data, indent=1, ensure_ascii=False))
