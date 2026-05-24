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
        parser.add_argument("--progress-every", type=int, default=1000)
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
        progress_every = options["progress_every"]
        dry_run = options["dry_run"]
        canonical_cache = {}

        total = None
        if not options["limit"]:
            total = queryset.count()
        else:
            total = min(options["limit"], queryset.count())

        self.stdout.write(
            f"Canonicalizing entity mentions: total={total}, dry_run={dry_run}, "
            f"batch_size={batch_size}"
        )

        for mention in queryset.iterator(chunk_size=batch_size):
            inspected += 1
            cache_key = (mention.entity_type, mention.entity_name.strip().casefold())
            if cache_key not in canonical_cache:
                canonical = resolve_canonical_entity(
                    mention.entity_name,
                    mention.entity_type,
                    create=not dry_run,
                    update_aliases=not dry_run,
                )
                canonical_cache[cache_key] = canonical.normalized_name if canonical else None

            canonical_name = canonical_cache[cache_key]
            if not canonical_name or mention.normalized_name == canonical_name:
                if progress_every and inspected % progress_every == 0:
                    self.stdout.write(
                        f"Progress: inspected={inspected}/{total}, updated={updated}, "
                        f"cached_entities={len(canonical_cache)}"
                    )
                continue

            old_name = mention.normalized_name
            mention.normalized_name = canonical_name
            updated += 1
            changed_pairs[(old_name, canonical_name)] += 1

            if dry_run:
                if progress_every and inspected % progress_every == 0:
                    self.stdout.write(
                        f"Progress: inspected={inspected}/{total}, updated={updated}, "
                        f"cached_entities={len(canonical_cache)}"
                    )
                continue

            batch.append(mention)
            if len(batch) >= batch_size:
                self._flush(batch)
                batch = []

            if progress_every and inspected % progress_every == 0:
                self.stdout.write(
                    f"Progress: inspected={inspected}/{total}, updated={updated}, "
                    f"cached_entities={len(canonical_cache)}"
                )

        if batch and not dry_run:
            self._flush(batch)

        for (old_name, new_name), count in sorted(changed_pairs.items(), key=lambda item: -item[1])[:25]:
            self.stdout.write(f"{old_name} -> {new_name}: {count}")

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"Done. Inspected: {inspected}. {action}: {updated}."))

    def _flush(self, mentions):
        EntityMention.objects.bulk_update(mentions, ["normalized_name"])
