"""
Reusable database operations for the scraper.
All queries use async SQLAlchemy sessions.
"""

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import select, exists
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Source, Article, Category, Tag, Location,
    ArticleCategory, ArticleTag, ArticleLocation, FetchRunLog,
    LanguageEnum, RunStatusEnum, FetcherUsedEnum
)
from utils.helpers import slugify, generate_slug


# ---------------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------------

async def get_active_sources(session: AsyncSession) -> list[Source]:
    result = await session.execute(
        select(Source)
        .where(Source.is_active == True)
        .order_by(Source.priority.asc())
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# DUPLICATE CHECK
# ---------------------------------------------------------------------------

async def url_exists(session: AsyncSession, url: str) -> bool:
    result = await session.execute(
        select(exists().where(Article.url == url))
    )
    return result.scalar()


# ---------------------------------------------------------------------------
# CATEGORIES
# ---------------------------------------------------------------------------

async def get_or_create_category(
    session: AsyncSession,
    name: str,
    parent_id: Optional[int] = None
) -> Category:
    slug = slugify(name)
    await session.execute(
        pg_insert(Category)
        .values(name=name, slug=slug, parent_id=parent_id, created_at=datetime.utcnow())
        .on_conflict_do_nothing(index_elements=["name"])
    )
    await session.flush()
    result = await session.execute(select(Category).where(Category.name == name))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# TAGS
# ---------------------------------------------------------------------------

async def get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    slug = slugify(name)
    await session.execute(
        pg_insert(Tag)
        .values(name=name, slug=slug, created_at=datetime.utcnow())
        .on_conflict_do_nothing(index_elements=["name"])
    )
    await session.flush()
    result = await session.execute(select(Tag).where(Tag.name == name))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# LOCATIONS
# ---------------------------------------------------------------------------

async def get_or_create_location(
    session: AsyncSession,
    name: str,
    loc_type: str,
    parent_id: Optional[int] = None,
    country_code: Optional[str] = None
) -> Location:
    parent_filter = (
        Location.parent_id == parent_id
        if parent_id is not None
        else Location.parent_id.is_(None)
    )
    # SELECT first — avoids the NULL uniqueness problem where PostgreSQL's
    # unique constraint allows duplicate (name, type, NULL) rows because
    # NULL != NULL, so ON CONFLICT never fires for top-level locations.
    result = await session.execute(
        select(Location)
        .where(Location.name == name, Location.type == loc_type, parent_filter)
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    await session.execute(
        pg_insert(Location)
        .values(
            name=name,
            type=loc_type,
            parent_id=parent_id,
            country_code=country_code,
            created_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing(constraint="uq_location_name_type_parent")
    )
    await session.flush()

    # LIMIT 1 — guards against any race-condition duplicates that slipped through
    result = await session.execute(
        select(Location)
        .where(Location.name == name, Location.type == loc_type, parent_filter)
        .limit(1)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# ARTICLES
# ---------------------------------------------------------------------------

async def save_article(
    session: AsyncSession,
    source_id: int,
    url: str,
    title: str,
    language: str,
    short_description: Optional[str] = None,
    body: Optional[str] = None,
    image_url: Optional[str] = None,
    published_at: Optional[datetime] = None,
    category_ids: list[int] = None,
    tag_ids: list[int] = None,
    location_ids: list[int] = None,
) -> Article:
    """
    Insert article and all M2M relations in a single transaction block.
    Caller is responsible for committing.
    """
    # Strip timezone from published_at — DB column is TIMESTAMP WITHOUT TIME ZONE
    from datetime import timezone as _tz
    if published_at is not None and published_at.tzinfo is not None:
        published_at = published_at.astimezone(_tz.utc).replace(tzinfo=None)

    article = Article(
        id=uuid.uuid4(),
        source_id=source_id,
        url=url,
        slug=generate_slug(title),
        title=title,
        short_description=short_description,
        body=body,
        image_url=image_url,
        language=LanguageEnum(language),
        published_at=published_at,
        scraped_at=datetime.utcnow(),
        is_published=True,
    )
    session.add(article)
    await session.flush()

    if category_ids:
        for cat_id in set(category_ids):
            session.add(ArticleCategory(article_id=article.id, category_id=cat_id))

    if tag_ids:
        for tag_id in set(tag_ids):
            session.add(ArticleTag(article_id=article.id, tag_id=tag_id))

    if location_ids:
        for loc_id in set(location_ids):
            session.add(ArticleLocation(article_id=article.id, location_id=loc_id))

    return article


# ---------------------------------------------------------------------------
# FETCH RUN LOGS
# ---------------------------------------------------------------------------

async def create_run_log(
    session: AsyncSession,
    run_id: uuid.UUID,
    source_id: int,
    started_at: datetime,
) -> FetchRunLog:
    log = FetchRunLog(
        run_id=run_id,
        source_id=source_id,
        started_at=started_at,
    )
    session.add(log)
    await session.flush()
    return log


async def finalize_run_log(
    session: AsyncSession,
    log: FetchRunLog,
    status: str,
    fetcher_used: Optional[str],
    urls_found: int,
    articles_saved: int,
    duplicates_skipped: int,
    errors_skipped: int,
    error_detail: Optional[str] = None,
) -> None:
    log.finished_at = datetime.utcnow()
    log.status = RunStatusEnum(status)
    log.fetcher_used = FetcherUsedEnum(fetcher_used) if fetcher_used else None
    log.urls_found = urls_found
    log.articles_saved = articles_saved
    log.duplicates_skipped = duplicates_skipped
    log.errors_skipped = errors_skipped
    log.error_detail = error_detail
    await session.flush()
