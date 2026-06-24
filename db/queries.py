"""
Reusable database operations for the scraper.
All queries use async SQLAlchemy sessions.
"""

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Source, Article, Category, Tag, Location,
    ArticleCategory, ArticleTag, ArticleLocation, FetchRunLog,
    LanguageEnum, RunStatusEnum, FetcherUsedEnum
)
from utils.helpers import slugify


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
    result = await session.execute(
        select(Category).where(Category.slug == slug)
    )
    category = result.scalar_one_or_none()
    if not category:
        category = Category(name=name, slug=slug, parent_id=parent_id)
        session.add(category)
        await session.flush()
    return category


# ---------------------------------------------------------------------------
# TAGS
# ---------------------------------------------------------------------------

async def get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    slug = slugify(name)
    result = await session.execute(
        select(Tag).where(Tag.slug == slug)
    )
    tag = result.scalar_one_or_none()
    if not tag:
        tag = Tag(name=name, slug=slug)
        session.add(tag)
        await session.flush()
    return tag


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
    result = await session.execute(
        select(Location).where(
            Location.name == name,
            Location.type == loc_type,
            Location.parent_id == parent_id
        )
    )
    location = result.scalar_one_or_none()
    if not location:
        location = Location(
            name=name,
            type=loc_type,
            parent_id=parent_id,
            country_code=country_code
        )
        session.add(location)
        await session.flush()
    return location


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
    article = Article(
        id=uuid.uuid4(),
        source_id=source_id,
        url=url,
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
