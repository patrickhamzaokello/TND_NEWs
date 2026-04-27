#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/tndnews_project}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BACKEND_SERVICE="${BACKEND_SERVICE:-tnd_backend_app}"
CELERY_SERVICE="${CELERY_SERVICE:-celery}"
BEAT_SERVICE="${BEAT_SERVICE:-beat}"

RUN_GIT_PULL="${RUN_GIT_PULL:-true}"
RUN_BACKUP="${RUN_BACKUP:-false}"
RUN_COLLECTSTATIC="${RUN_COLLECTSTATIC:-true}"
RUN_CLEANUP="${RUN_CLEANUP:-true}"
CLEANUP_OLDER_THAN_HOURS="${CLEANUP_OLDER_THAN_HOURS:-0}"
CLEANUP_LIMIT="${CLEANUP_LIMIT:-0}"
RUN_DIGEST_SMOKE="${RUN_DIGEST_SMOKE:-false}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

step() {
  printf "\n==> %s\n" "$1"
}

require_project() {
  if [[ ! -f "$PROJECT_DIR/$COMPOSE_FILE" ]]; then
    echo "Cannot find $COMPOSE_FILE in $PROJECT_DIR" >&2
    exit 1
  fi
}

main() {
  require_project
  cd "$PROJECT_DIR"

  step "Checking Docker Compose configuration"
  compose config --quiet

  if [[ "$RUN_GIT_PULL" == "true" ]]; then
    step "Pulling latest code"
    git pull --ff-only
  fi

  if [[ "$RUN_BACKUP" == "true" ]]; then
    step "Creating database backup"
    mkdir -p backups
    compose exec -T postgres_db pg_dump -U "${POSTGRES_USER:-tnd_user}" "${POSTGRES_DB:-tnd}" > "backups/tnd_$(date +%Y%m%d_%H%M%S).sql"
  fi

  step "Building backend image"
  compose build "$BACKEND_SERVICE"

  step "Starting database and Redis"
  compose up -d postgres_db redis

  step "Applying migrations"
  compose run --rm "$BACKEND_SERVICE" python manage.py migrate --noinput

  step "Running Django system check"
  compose run --rm "$BACKEND_SERVICE" python manage.py check

  if [[ "$RUN_COLLECTSTATIC" == "true" ]]; then
    step "Collecting static files"
    compose run --rm "$BACKEND_SERVICE" python manage.py collectstatic --noinput
  fi

  step "Backfilling article identity fields"
  compose run --rm "$BACKEND_SERVICE" python manage.py backfill_article_identity

  step "Syncing Celery Beat schedules"
  compose run --rm "$BACKEND_SERVICE" python manage.py sync_newsintelligence_schedule

  if [[ "$RUN_CLEANUP" == "true" ]]; then
    step "Cleaning articles without full content"
    cleanup_args=(python manage.py cleanup_incomplete_articles --delete "--older-than-hours=$CLEANUP_OLDER_THAN_HOURS")
    if [[ "$CLEANUP_LIMIT" != "0" ]]; then
      cleanup_args+=("--limit=$CLEANUP_LIMIT")
    fi
    compose run --rm "$BACKEND_SERVICE" "${cleanup_args[@]}"
  fi

  step "Reloading backend, Celery worker, and Celery Beat"
  compose up -d --remove-orphans "$BACKEND_SERVICE" "$CELERY_SERVICE" "$BEAT_SERVICE"

  step "Verifying services"
  compose ps

  step "Verifying scheduled jobs"
  compose exec -T "$BACKEND_SERVICE" python manage.py shell -c "from django_celery_beat.models import PeriodicTask; [print(t.name, t.task, t.enabled, t.crontab or t.interval) for t in PeriodicTask.objects.filter(name__in=['generate-daily-digest','enrich-articles-hourly','scrape-daily-monitor-news']).order_by('name')]"

  if [[ "$RUN_DIGEST_SMOKE" == "true" ]]; then
    step "Running optional enrichment and digest smoke test"
    compose exec -T "$BACKEND_SERVICE" python manage.py enrich_articles --batch-size 20
    compose exec -T "$BACKEND_SERVICE" python manage.py enrich_articles --digest
  fi

  step "Deployment complete"
}

main "$@"
