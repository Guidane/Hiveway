from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from builder import blueprint


class Command(BaseCommand):
    help = 'Apply a blueprint file (JSON, or an AI answer containing a ```json block).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true', help='Only show what would change.')

    def handle(self, path, dry_run=False, **options):
        try:
            text = Path(path).read_text(encoding='utf-8-sig')
            data = blueprint.parse_text(text)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc))
        result = blueprint.apply(data, dry_run=dry_run)
        if result.notes:
            self.stdout.write(f'Notes: {result.notes}')
        if not result.ok:
            for error in result.errors:
                self.stderr.write(f'  {error}')
            raise CommandError('Nothing was changed.')
        changes = result.summary()
        for line in changes or ['Nothing to change.']:
            self.stdout.write(f'  {line}')
        self.stdout.write(self.style.SUCCESS('Dry run: nothing was changed.' if dry_run else 'Applied.'))
