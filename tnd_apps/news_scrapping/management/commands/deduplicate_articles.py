"""
Management command: deduplicate_articles
=========================================

Scans the articles table for duplicates across four dimensions and removes
them, keeping the best copy in each duplicate group.

Duplicate types detected
------------------------

  1. SAME URL (exact)
     The ``url`` field is UNIQUE so this can only exist if the constraint was
     bypassed — but we check anyway to be safe.

  2. SAME CANONICAL URL
     Two rows whose normalised URLs (no www., no UTM params, trailing slash
     stripped) resolve to the same string.  A common result of scrapers saving
     the same article once as http and once as https, or with/without www.

  3. SAME EXTERNAL ID + SOURCE
     The model's ``unique_together`` covers this, but race conditions between
     parallel Celery workers can produce duplicates before the constraint fires.

  4. SAME TITLE (same source, within 30 days of each other)
     Two articles from the same outlet with an identical normalised title hash.
     This catches re-dated reposts and scraper retries that produced a slightly
     different URL slug.

"Best copy" selection
---------------------
When multiple rows match, the keeper is chosen by:
  1. ``has_full_content=True`` over False
  2. Highest ``word_count``
  3. Earliest ``scraped_at`` (oldest — most likely the original)

Usage examples
--------------

  # Audit only — print a report, touch nothing
  python manage.py deduplicate_articles

  # Show full detail for every duplicate group
  python manage.py deduplicate_articles --verbose

  # Limit audit to one source
  python manage.py deduplicate_articles --source "Daily Monitor"

  # Actually delete duplicates (requires explicit flag)
  python manage.py deduplicate_articles --delete

  # Delete duplicates for one source only
  python manage.py deduplicate_articles --delete --source "NilePost"

  # Wipe the Redis broadcast-dedup cache so cleaned articles can be
  # re-broadcast if needed (combine with --delete)
  python manage.py deduplicate_articles --delete --clear-broadcast-cache
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

try:
    from django.utils.termcolors import colorize
    GREEN  = lambda s: colorize(s, fg="green")
    YELLOW = lambda s: colorize(s, fg="yellow")
    CYAN   = lambda s: colorize(s, fg="cyan")
    RED    = lambda s: colorize(s, fg="red")
    BOLD   = lambda s: colorize(s, opts=("bold",))
except Exception:
    GREEN = YELLOW = CYAN = RED = BOLD = lambda s: s


class Command(BaseCommand):
    help = "Audit and optionally remove duplicate articles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            default=False,
            help="Actually delete duplicates. Without this flag the command is read-only.",
        )
        parser.add_argument(
            "--source",
            type=str,
            default=None,
            metavar="NAME",
            help="Limit to a specific NewsSource by name.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Print full detail for every duplicate group.",
        )
        parser.add_argument(
            "--clear-broadcast-cache",
            action="store_true",
            default=False,
            help=(
                "After deletion, remove the Redis broadcast-dedup keys for deleted "
                "articles so the kept copies can be re-broadcast if needed."
            ),
        )

    # ── Entry point ────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        from tnd_apps.news_scrapping.models import Article, NewsSource

        do_delete     = options["delete"]
        source_name   = options["source"]
        verbose       = options["verbose"]
        clear_cache   = options["clear_broadcast_cache"]

        self.stdout.write("")
        self.stdout.write(BOLD("=" * 64))
        self.stdout.write(
            f"  Article Deduplication  "
            f"[{BOLD(RED('LIVE DELETE')) if do_delete else BOLD(YELLOW('AUDIT ONLY — nothing deleted'))}]"
        )
        self.stdout.write(BOLD("=" * 64))

        qs = Article.objects.select_related("source", "category", "author")
        if source_name:
            try:
                source = NewsSource.objects.get(name=source_name)
                qs = qs.filter(source=source)
                self.stdout.write(f"  Filtering to source: {CYAN(source_name)}")
            except NewsSource.DoesNotExist:
                self.stdout.write(RED(f"  Source '{source_name}' not found. Aborting."))
                return

        self.stdout.write("")

        all_to_delete: set[int] = set()

        # ── 1. Exact URL duplicates ────────────────────────────────────────
        n, ids = self._find_duplicates_by(qs, "url", verbose)
        self.stdout.write(f"  [1] Exact URL duplicates          : {self._fmt(n)} groups, {self._fmt(len(ids))} to remove")
        all_to_delete |= ids

        # ── 2. Canonical URL duplicates ────────────────────────────────────
        n, ids = self._find_duplicates_by(qs, "canonical_url", verbose)
        self.stdout.write(f"  [2] Canonical URL duplicates      : {self._fmt(n)} groups, {self._fmt(len(ids))} to remove")
        all_to_delete |= ids

        # ── 3. external_id + source duplicates ────────────────────────────
        n, ids = self._find_external_id_dupes(qs, verbose)
        self.stdout.write(f"  [3] external_id + source dupes    : {self._fmt(n)} groups, {self._fmt(len(ids))} to remove")
        all_to_delete |= ids

        # ── 4. Same-title, same-source (30-day window) ────────────────────
        n, ids = self._find_title_dupes(qs, verbose)
        self.stdout.write(f"  [4] Same title+source (30 days)   : {self._fmt(n)} groups, {self._fmt(len(ids))} to remove")
        all_to_delete |= ids

        self.stdout.write("")
        self.stdout.write(
            f"  Total unique articles to delete   : {BOLD(RED(str(len(all_to_delete))) if all_to_delete else GREEN('0'))}"
        )
        self.stdout.write("")

        if not all_to_delete:
            self.stdout.write(GREEN("  No duplicates found. Database is clean."))
            self.stdout.write("")
            return

        if not do_delete:
            self.stdout.write(
                YELLOW("  Run with --delete to remove the duplicates listed above.")
            )
            self.stdout.write("")
            return

        # ── Perform deletion ───────────────────────────────────────────────
        self.stdout.write(f"  Deleting {len(all_to_delete)} duplicate articles …")

        with transaction.atomic():
            deleted_count, _ = Article.objects.filter(pk__in=all_to_delete).delete()

        self.stdout.write(GREEN(f"  Deleted {deleted_count} articles."))

        if clear_cache:
            self._clear_broadcast_keys(all_to_delete)

        self.stdout.write(BOLD("=" * 64))
        self.stdout.write("")

    # ── Duplicate finders ──────────────────────────────────────────────────

    def _find_duplicates_by(self, qs, field: str, verbose: bool) -> tuple[int, set[int]]:
        """
        Find groups with the same non-empty value for `field`.
        Returns (group_count, set_of_pks_to_delete).
        """
        from django.db.models import Count

        duped_values = (
            qs.exclude(**{field: ""})
            .values(field)
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
            .values_list(field, flat=True)
        )

        to_delete: set[int] = set()
        group_count = 0

        for value in duped_values:
            group = list(qs.filter(**{field: value}).order_by("id"))
            if len(group) < 2:
                continue
            group_count += 1
            keeper, dupes = self._pick_keeper(group)
            to_delete.update(a.pk for a in dupes)
            if verbose:
                self._print_group(field, value, keeper, dupes)

        return group_count, to_delete

    def _find_external_id_dupes(self, qs, verbose: bool) -> tuple[int, set[int]]:
        from django.db.models import Count

        pairs = (
            qs.exclude(external_id="")
            .values("external_id", "source_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )

        to_delete: set[int] = set()
        group_count = 0

        for pair in pairs:
            group = list(
                qs.filter(
                    external_id=pair["external_id"],
                    source_id=pair["source_id"],
                ).order_by("id")
            )
            if len(group) < 2:
                continue
            group_count += 1
            keeper, dupes = self._pick_keeper(group)
            to_delete.update(a.pk for a in dupes)
            if verbose:
                self._print_group(
                    "external_id+source",
                    f"{pair['external_id']} / source_id={pair['source_id']}",
                    keeper,
                    dupes,
                )

        return group_count, to_delete

    def _find_title_dupes(self, qs, verbose: bool) -> tuple[int, set[int]]:
        """
        Find articles with the same normalized_title_hash from the same source
        that were scraped within 30 days of each other.
        """
        from django.db.models import Count

        hashed = (
            qs.exclude(normalized_title_hash="")
            .values("normalized_title_hash", "source_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )

        to_delete: set[int] = set()
        group_count = 0
        window = timezone.timedelta(days=30)

        for row in hashed:
            candidates = list(
                qs.filter(
                    normalized_title_hash=row["normalized_title_hash"],
                    source_id=row["source_id"],
                ).order_by("scraped_at")
            )
            if len(candidates) < 2:
                continue

            # Split into clusters where each article is within 30 days of the
            # previous one.  Articles far apart in time are legitimately the
            # same headline used again for a new story.
            clusters = self._cluster_by_time(candidates, window)
            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                group_count += 1
                keeper, dupes = self._pick_keeper(cluster)
                to_delete.update(a.pk for a in dupes)
                if verbose:
                    self._print_group(
                        "title+source (30d)",
                        cluster[0].title[:60],
                        keeper,
                        dupes,
                    )

        return group_count, to_delete

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _cluster_by_time(articles, window):
        """Group a time-ordered list into runs where consecutive items are within `window`."""
        if not articles:
            return []
        clusters = [[articles[0]]]
        for art in articles[1:]:
            prev = clusters[-1][-1]
            if art.scraped_at and prev.scraped_at:
                gap = abs((art.scraped_at - prev.scraped_at))
                if gap <= window:
                    clusters[-1].append(art)
                    continue
            clusters.append([art])
        return clusters

    @staticmethod
    def _pick_keeper(group: list) -> tuple:
        """
        Select the best article from a duplicate group.

        Priority:
          1. has_full_content=True
          2. highest word_count
          3. earliest scraped_at (original)
        """
        def score(a):
            return (
                int(a.has_full_content),   # higher = better
                a.word_count,              # higher = better
                -(a.scraped_at.timestamp() if a.scraped_at else 0),  # earlier = better
            )

        sorted_group = sorted(group, key=score, reverse=True)
        keeper = sorted_group[0]
        dupes  = sorted_group[1:]
        return keeper, dupes

    def _print_group(self, dimension: str, value, keeper, dupes: list) -> None:
        self.stdout.write(f"    {BOLD(dimension)}: {CYAN(str(value)[:80])}")
        self.stdout.write(
            f"      KEEP [{keeper.pk}] {keeper.title[:55]!r}  "
            f"words={keeper.word_count}  full={keeper.has_full_content}  "
            f"scraped={keeper.scraped_at:%Y-%m-%d %H:%M}" if keeper.scraped_at else ""
        )
        for d in dupes:
            self.stdout.write(
                f"      {RED('DEL')}  [{d.pk}] {d.title[:55]!r}  "
                f"words={d.word_count}  full={d.has_full_content}  "
                f"scraped={d.scraped_at:%Y-%m-%d %H:%M}" if d.scraped_at else ""
            )
        self.stdout.write("")

    @staticmethod
    def _clear_broadcast_keys(pks: set[int]) -> None:
        try:
            from django.core.cache import cache
            keys = [f"ws_broadcast:article:{pk}" for pk in pks]
            cache.delete_many(keys)
        except Exception:
            pass

    @staticmethod
    def _fmt(n: int) -> str:
        return GREEN("0") if n == 0 else YELLOW(str(n))
