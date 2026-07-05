"""
Management command: send_digest_email

Usage examples
--------------
# Send today's digest to all active subscribers (dry-run first):
    python manage.py send_digest_email --dry-run

# Send for real to all subscribers:
    python manage.py send_digest_email

# Send to a specific test email only (does not touch subscriber records):
    python manage.py send_digest_email --to me@example.com

# Send a specific date's digest:
    python manage.py send_digest_email --date 2026-07-04

# Add a subscriber on the fly and send:
    python manage.py send_digest_email --subscribe me@example.com --name "Patrick"

# List current subscribers:
    python manage.py send_digest_email --list-subscribers
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Send the daily digest email to subscribers (or a test address)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            metavar='YYYY-MM-DD',
            help='Digest date to send (default: today)',
        )
        parser.add_argument(
            '--to',
            metavar='EMAIL',
            help='Send a one-off test email to this address instead of all subscribers',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending anything',
        )
        parser.add_argument(
            '--subscribe',
            metavar='EMAIL',
            help='Create (or confirm) a subscriber with this email, then send',
        )
        parser.add_argument(
            '--name',
            metavar='NAME',
            help='Display name for --subscribe (optional)',
        )
        parser.add_argument(
            '--list-subscribers',
            action='store_true',
            help='Print subscriber list and exit',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Send even if the subscriber already received today\'s digest',
        )

    def handle(self, *args, **options):
        from tnd_apps.newsintelligence.models import DailyDigest, DigestSubscriber
        from tnd_apps.newsintelligence.email_service import send_digest_to_all, send_digest_to_email

        # ── List subscribers ──────────────────────────────────────────────────
        if options['list_subscribers']:
            subs = DigestSubscriber.objects.order_by('-subscribed_at')
            if not subs.exists():
                self.stdout.write('No subscribers yet.')
                return
            self.stdout.write(self.style.SUCCESS(f'{"Email":<40} {"Active":<8} {"Confirmed":<10} {"Sent":<6} {"Last sent"}'))
            self.stdout.write('-' * 85)
            for s in subs:
                self.stdout.write(
                    f'{s.email:<40} {"yes" if s.is_active else "no":<8}'
                    f' {"yes" if s.confirmed else "no":<10}'
                    f' {s.emails_sent:<6}'
                    f' {s.last_sent_at.strftime("%Y-%m-%d %H:%M") if s.last_sent_at else "never"}'
                )
            return

        # ── Resolve digest date ───────────────────────────────────────────────
        if options['date']:
            try:
                target_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError(f"Invalid date format: {options['date']!r}. Use YYYY-MM-DD.")
        else:
            target_date = timezone.localdate()

        digest = DailyDigest.objects.filter(digest_date=target_date, is_published=True).first()
        if not digest:
            unpublished = DailyDigest.objects.filter(digest_date=target_date).first()
            if unpublished:
                raise CommandError(
                    f'Digest for {target_date} exists but is NOT published '
                    f'(status={unpublished.editorial_review_status}). '
                    f'Use the admin to approve it first, or pass --force to override.'
                )
            raise CommandError(
                f'No published digest found for {target_date}. '
                f'Run: python manage.py generate_daily_digest first.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Digest: {digest.digest_date} | {digest.articles_analyzed} articles | '
                f'{len(digest.top_stories)} top stories'
            )
        )

        # ── Optional: add/confirm a subscriber ───────────────────────────────
        if options['subscribe']:
            email = options['subscribe'].strip().lower()
            sub, created = DigestSubscriber.objects.get_or_create(
                email=email,
                defaults={'name': options.get('name') or '', 'confirmed': True},
            )
            if not created:
                if not sub.confirmed:
                    sub.confirmed = True
                    sub.confirmed_at = timezone.now()
                    sub.save(update_fields=['confirmed', 'confirmed_at'])
                if not sub.is_active:
                    sub.is_active = True
                    sub.save(update_fields=['is_active'])
            verb = 'Created' if created else 'Updated'
            self.stdout.write(f'{verb} subscriber: {sub.email}')

        # ── Test send to specific address ─────────────────────────────────────
        if options['to']:
            self.stdout.write(f'Sending test email to {options["to"]} ...')
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('[DRY RUN] Would send to: ' + options['to']))
                return
            ok = send_digest_to_email(digest, options['to'])
            if ok:
                self.stdout.write(self.style.SUCCESS(f'Test email sent to {options["to"]}'))
            else:
                raise CommandError(f'Failed to send test email — check logs for details.')
            return

        # ── Bulk send to all subscribers ──────────────────────────────────────
        qs = DigestSubscriber.objects.filter(is_active=True, confirmed=True)
        if not options['force']:
            qs = qs.exclude(last_digest_date=target_date)

        total = qs.count()
        if total == 0:
            self.stdout.write('No eligible subscribers to send to.')
            return

        self.stdout.write(f'Eligible subscribers: {total}')

        if options['dry_run']:
            for sub in qs.values_list('email', flat=True):
                self.stdout.write(f'  [DRY RUN] Would send to: {sub}')
            self.stdout.write(self.style.WARNING(f'[DRY RUN] {total} emails would be sent.'))
            return

        # Temporarily force-include by clearing last_digest_date if --force
        if options['force']:
            # Re-use send_digest_to_email per subscriber to bypass the date check
            sent = failed = 0
            for sub in qs:
                ok = send_digest_to_email(digest, sub.email)
                if ok:
                    sub.mark_sent(target_date)
                    sent += 1
                else:
                    failed += 1
        else:
            result = send_digest_to_all(digest)
            sent = result['sent']
            failed = result['failed']

        self.stdout.write(
            self.style.SUCCESS(f'Done — sent: {sent}, failed: {failed}')
        )
        if failed:
            self.stdout.write(self.style.WARNING('Check Django logs for details on failures.'))
