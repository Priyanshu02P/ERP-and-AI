"""`python manage.py generate_recurring_pos` - run the recurring-release generator now (same as the daily cron)."""
from django.core.management.base import BaseCommand

from apps.purchasing.services import generate_due_releases


class Command(BaseCommand):
    help = "Create release POs for every recurring agreement that is due."

    def handle(self, *args, **options):
        result = generate_due_releases()
        self.stdout.write(self.style.SUCCESS(f"{result['releases_created']} release(s) created, "
                                             f"{result['agreements_closed']} agreement(s) completed."))
