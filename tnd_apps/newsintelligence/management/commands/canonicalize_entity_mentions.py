from collections import defaultdict

from django.core.management.base import BaseCommand

from tnd_apps.newsintelligence.entity_canonicalization import resolve_canonical_entity
from tnd_apps.newsintelligence.models import EntityMention


class Command(BaseCommand):
    help = "Resolve existing entity mentions to canonical entities and aliases."

    def add_arguments(self, parser):
        parser.add_argument("--entity-type", choices=["person", "organization", "location"])
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        queryset = EntityMention.objects.all().order_by("id")
        if options["entity_type"]:
            queryset = queryset.filter(entity_type=options["entity_type"])
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        inspected = 0
        updated = 0
        batch = []
        changed_pairs = defaultdict(int)
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        for mention in queryset.iterator(chunk_size=batch_size):
            inspected += 1
            canonical = resolve_canonical_entity(
                mention.entity_name,
                mention.entity_type,
                create=not dry_run,
                update_aliases=not dry_run,
            )
            if not canonical or mention.normalized_name == canonical.normalized_name:
                continue

            old_name = mention.normalized_name
            mention.normalized_name = canonical.normalized_name
            updated += 1
            changed_pairs[(old_name, canonical.normalized_name)] += 1

            if dry_run:
                continue

            batch.append(mention)
            if len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if batch and not dry_run:
            self._flush(batch)

        for (old_name, new_name), count in sorted(changed_pairs.items(), key=lambda item: -item[1])[:25]:
            self.stdout.write(f"{old_name} -> {new_name}: {count}")

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"Done. Inspected: {inspected}. {action}: {updated}."))

    def _flush(self, mentions):
        EntityMention.objects.bulk_update(mentions, ["normalized_name"])
