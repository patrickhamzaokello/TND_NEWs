from django.core.management.base import BaseCommand

from tnd_apps.newsintelligence.entity_canonicalization import (
    clean_entity_display_name,
    entity_alias_keys,
    normalize_entity_name,
)
from tnd_apps.newsintelligence.models import Entity, EntityMention


class Command(BaseCommand):
    help = "Merge known aliases into one canonical entity and update matching mentions."

    def add_arguments(self, parser):
        parser.add_argument("--canonical", required=True, help="Canonical display name.")
        parser.add_argument("--type", required=True, choices=["person", "organization", "location"])
        parser.add_argument(
            "--alias",
            action="append",
            default=[],
            help="Alias display name. Can be passed multiple times.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        entity_type = options["type"]
        canonical_name = clean_entity_display_name(options["canonical"])
        alias_names = [clean_entity_display_name(alias) for alias in options["alias"] if clean_entity_display_name(alias)]
        canonical_key = normalize_entity_name(canonical_name, entity_type)

        if not canonical_key:
            self.stderr.write(self.style.ERROR("Canonical name could not be normalized."))
            return

        alias_keys = set()
        for name in [canonical_name, *alias_names]:
            alias_keys.update(entity_alias_keys(name, entity_type))
            alias_keys.add(name)

        existing = Entity.objects.filter(entity_type=entity_type, normalized_name=canonical_key).first()
        if not existing and not options["dry_run"]:
            existing = Entity.objects.create(
                name=canonical_name,
                normalized_name=canonical_key,
                entity_type=entity_type,
                aliases=sorted(alias_keys),
            )
        elif existing and not options["dry_run"]:
            existing.name = canonical_name
            existing.aliases = sorted(set(existing.aliases or []) | alias_keys)
            existing.save(update_fields=["name", "aliases", "updated_at"])

        mention_filter_keys = {
            normalize_entity_name(name, entity_type)
            for name in [canonical_name, *alias_names]
            if normalize_entity_name(name, entity_type)
        }
        mention_names = [canonical_name, *alias_names]
        mentions = EntityMention.objects.filter(entity_type=entity_type).filter(
            normalized_name__in=mention_filter_keys
        ) | EntityMention.objects.filter(entity_type=entity_type, entity_name__in=mention_names)
        mentions = mentions.distinct()

        count = mentions.count()
        if options["dry_run"]:
            self.stdout.write(
                f"Would merge {count} mentions into {canonical_name} ({canonical_key}). "
                f"Aliases: {sorted(alias_keys)}"
            )
            return

        updated = mentions.update(normalized_name=canonical_key)
        self.stdout.write(
            self.style.SUCCESS(
                f"Merged {updated} mentions into {canonical_name} ({canonical_key}). "
                f"Aliases: {sorted(alias_keys)}"
            )
        )
