"""Try the GST lookup from the terminal, without opening the app.

    python manage.py check_gst 29ABCDE1234F1Z5

Shows exactly what the provider returned and what the app would fill in.
"""
from django.core.management.base import BaseCommand, CommandError

from customers import gst


class Command(BaseCommand):
    help = 'Look up one GST number with the configured provider and print the result.'

    def add_arguments(self, parser):
        parser.add_argument('gstin')

    def handle(self, *args, **options):
        gstin = gst.normalize_gstin(options['gstin'])
        problem = gst.gstin_problem(gstin)
        if problem:
            raise CommandError(problem)
        self.stdout.write(f'Format OK. State: {gst.state_name(gstin)}')
        if not gst.lookup_configured():
            raise CommandError(
                'No GST provider is set. Put GST_LOOKUP_URL and GST_LOOKUP_API_KEY in the .env file '
                '(copy .env.example) and run this again.'
            )
        try:
            info = gst.fetch_taxpayer(gstin)
        except gst.GstNotFound:
            raise CommandError('The provider has no business for this GST number (or answered with an error).')
        except gst.GstUnavailable as exc:
            raise CommandError(f'Lookup failed: {exc}')
        for key, value in info.items():
            self.stdout.write(f'  {key:<11} {value or "-"}')
        self.stdout.write(self.style.SUCCESS('Lookup works.'))
