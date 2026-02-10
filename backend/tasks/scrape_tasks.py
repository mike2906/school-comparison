# Celery scrape tasks - Phase 2
# Scheduled jobs for scraping schools
from tasks import celery_app


@celery_app.task
def scrape_registry():
    """Scrape the school registry for new/updated schools."""
    pass


@celery_app.task
def scrape_school_website(school_id: int):
    """Scrape a single school's website for prices and details."""
    pass


@celery_app.task
def scrape_nvo_results():
    """Scrape NVO exam results from MoE platform."""
    pass
