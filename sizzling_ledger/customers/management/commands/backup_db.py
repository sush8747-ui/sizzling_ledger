"""Make a safe copy of the SQLite database.

    python manage.py backup_db            # -> backups/db-YYYYmmdd-HHMMSS.sqlite3
    python manage.py backup_db --keep 30  # keep the newest 30 copies
    python manage.py backup_db --dir D:\\Backups

It only READS the live database (using SQLite's own backup API, which is safe
even while the server is running) and writes a new file. Nothing in the
database is changed. To restore: stop the server, then copy a backup over
db.sqlite3.
"""
import sqlite3
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Copy db.sqlite3 into a timestamped backup file and keep only the newest copies.'

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=14, help='How many backups to keep (default 14).')
        parser.add_argument('--dir', default=None, help='Folder for the backups (default: <project>/backups).')

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        if 'sqlite3' not in db['ENGINE']:
            raise CommandError('backup_db only supports the SQLite database.')
        keep = options['keep']
        if keep < 1:
            raise CommandError('--keep must be at least 1.')

        source_path = Path(db['NAME'])
        if not source_path.exists():
            raise CommandError(f'Database file not found: {source_path}')
        out_dir = Path(options['dir']) if options['dir'] else Path(settings.BASE_DIR) / 'backups'
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"db-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"

        source = sqlite3.connect(str(source_path))
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        old = sorted(out_dir.glob('db-*.sqlite3'))
        for stale in old[:-keep]:
            try:
                stale.unlink()
            except OSError:
                pass
        self.stdout.write(self.style.SUCCESS(f'Backup saved: {dest}'))
