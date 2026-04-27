import json

from celery.schedules import crontab, schedule
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

from TNDNEWS.celery import celery_app


class Command(BaseCommand):
    help = "Sync Celery beat schedule entries from TNDNEWS.celery into django-celery-beat."

    def handle(self, *args, **options):
        synced = 0
        for name, entry in celery_app.conf.beat_schedule.items():
            task_name = entry["task"]
            kwargs = entry.get("kwargs") or {}
            defaults = {
                "task": task_name,
                "kwargs": json.dumps(kwargs),
                "enabled": True,
            }

            schedule_value = entry["schedule"]
            if isinstance(schedule_value, crontab):
                defaults["crontab"] = self._get_crontab(schedule_value)
                defaults["interval"] = None
            elif isinstance(schedule_value, (int, float)):
                defaults["interval"] = self._get_interval(int(schedule_value))
                defaults["crontab"] = None
            elif isinstance(schedule_value, schedule):
                defaults["interval"] = self._get_interval(int(schedule_value.run_every.total_seconds()))
                defaults["crontab"] = None
            else:
                self.stdout.write(self.style.WARNING(f"Skipped unsupported schedule for {name}: {schedule_value!r}"))
                continue

            PeriodicTask.objects.update_or_create(name=name, defaults=defaults)
            synced += 1

        self.stdout.write(self.style.SUCCESS(f"Synced {synced} periodic tasks."))

    def _get_crontab(self, value):
        return CrontabSchedule.objects.get_or_create(
            minute=str(value._orig_minute),
            hour=str(value._orig_hour),
            day_of_week=str(value._orig_day_of_week),
            day_of_month=str(value._orig_day_of_month),
            month_of_year=str(value._orig_month_of_year),
            timezone=celery_app.conf.timezone,
        )[0]

    def _get_interval(self, seconds):
        return IntervalSchedule.objects.get_or_create(
            every=seconds,
            period=IntervalSchedule.SECONDS,
        )[0]
