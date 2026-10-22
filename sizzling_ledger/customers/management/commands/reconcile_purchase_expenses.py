from django.core.management.base import BaseCommand
from django.db.models import Sum

from customers.models import Invoice


class Command(BaseCommand):
    help = (
        "Recomputes post_account_balance for every account from the actual "
        "sum of its currently-pending (unprocessed) PurchaseExpense records. "
        "Fixes drift left behind by deleted or edited pending entries. "
        "Safe to run repeatedly."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Show what would change without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        fixed = 0
        for account in Invoice.objects.all():
            purchases_total = account.purchases_expenses.filter(processed=False).aggregate(
                total=Sum('amount')
            )['total'] or 0
            salaries_total = account.salaries.filter(processed=False).aggregate(
                total=Sum('amount')
            )['total'] or 0
            correct = purchases_total + salaries_total

            if account.post_account_balance != correct:
                self.stdout.write(
                    f"{account.name}: stored={account.post_account_balance}  "
                    f"correct={correct}"
                )
                if not dry_run:
                    account.post_account_balance = correct
                    account.save(update_fields=['post_account_balance'])
                fixed += 1

        if fixed == 0:
            self.stdout.write(self.style.SUCCESS("Everything already matches. Nothing to fix."))
        elif dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run - {fixed} account(s) would be corrected."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Corrected {fixed} account(s)."))