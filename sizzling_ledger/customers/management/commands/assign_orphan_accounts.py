"""
Management command to assign orphan Invoice rows (where user is null) to a specified user.
This ensures no accounts are invisible to everyone.

Usage:
    python manage.py assign_orphan_accounts --user-email admin@example.com [--dry-run]
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from customers.models import Invoice


class Command(BaseCommand):
    help = 'Assign orphan accounts (where user is null) to a specified user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-email',
            type=str,
            required=True,
            help='Email of the user to assign orphan accounts to',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making actual changes',
        )

    def handle(self, *args, **options):
        user_email = options['user_email']
        dry_run = options['dry_run']

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            raise CommandError(f'User with email {user_email} does not exist')

        orphan_accounts = Invoice.objects.filter(user__isnull=True)
        count = orphan_accounts.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('No orphan accounts found'))
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: Would assign {count} orphan account(s) to {user.email}:'
                )
            )
            for account in orphan_accounts:
                self.stdout.write(f'  - {account.id}: {account.name}')
        else:
            orphan_accounts.update(user=user)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully assigned {count} orphan account(s) to {user.email}'
                )
            )
            for account in orphan_accounts:
                self.stdout.write(f'  - {account.id}: {account.name}')
