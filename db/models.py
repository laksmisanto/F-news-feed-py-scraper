"""
SQLAlchemy ORM models for news scraper.
All tables follow the agreed schema from architecture review.

Recent additions:
- Source.crawl_enabled    → BOOL toggle to enable domain crawler per source
- Source.crawl_config     → JSON crawler tuning (seed paths, depth, patterns)
- FetcherUsedEnum.crawler → enum value for the new crawler fetcher
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, Integer, Text, DateTime,
    ForeignKey, UniqueConstraint, JSON, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class LanguageEnum(str, enum.Enum):
    bn = "bn"
    en = "en"


class FetcherUsedEnum(str, enum.Enum):
    rss = "rss"
    sitemap = "sitemap"
    html = "html"
    crawler = "crawler"   # NEW: domain crawler


class RunStatusEnum(str, enum.Enum):
    success = "success"
    partial = "partial"
    failed = "failed"


class LocationTypeEnum(str, enum.Enum):
    city = "city"
    district = "district"
    division = "division"
    country = "country"


# ---------------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    language = Column(SAEnum(LanguageEnum), nullable=False)
    base_url = Column(String(500), nullable=False)
    rss_url = Column(String(500), nullable=True)
    sitemap_url = Column(String(500), nullable=True)
    # JSON: { article_list, title, body, image, date } CSS selectors
    html_scrape_config = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # Lower number = higher priority
    priority = Column(Integer, default=10, nullable=False)

    # ─── NEW: domain crawler toggle + config ──────────────────────────────
    crawl_enabled = Column(Boolean, default=False, nullable=False)
    # crawl_config JSON shape (all keys optional, sensible defaults applied):
    # {
    #   "seed_paths":           ["/", "/sports", "/business"],
    #   "max_depth":            2,
    #   "max_pages_per_run":    100,
    #   "rate_limit_seconds":   1.0,
    #   "article_url_patterns": ["/article/", "/\\d{4}/\\d{2}/"],
    #   "exclude_patterns":     ["/tag/", "/author/", "/page/"],
    #   "respect_robots":       true
    # }
    crawl_config = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    articles = relationship("Article", back_populates="source")
    fetch_run_logs = relationship("FetchRunLog", back_populates="source")


# ---------------------------------------------------------------------------
# CATEGORIES  (self-referencing parent → child)
# ---------------------------------------------------------------------------

class Category(Base):
    __tablename__ = "scraper_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)
    parent_id = Column(Integer, ForeignKey("scraper_categories.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship("Category", back_populates="parent")
    articles = relationship("ArticleCategory", back_populates="category")


# ---------------------------------------------------------------------------
# TAGS  (open / dynamic)
# ---------------------------------------------------------------------------

class Tag(Base):
    __tablename__ = "scraper_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    articles = relationship("ArticleTag", back_populates="tag")


# ---------------------------------------------------------------------------
# LOCATIONS  (BD hierarchy + international country)
# ---------------------------------------------------------------------------

class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    type = Column(SAEnum(LocationTypeEnum), nullable=False)
    parent_id = Column(Integer, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    country_code = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    parent = relationship("Location", remote_side=[id], back_populates="children")
    children = relationship("Location", back_populates="parent")
    articles = relationship("ArticleLocation", back_populates="location")

    __table_args__ = (
        UniqueConstraint("name", "type", "parent_id", name="uq_location_name_type_parent"),
    )


# ---------------------------------------------------------------------------
# ARTICLES
# ---------------------------------------------------------------------------

class Article(Base):
    __tablename__ = "scraped_articles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    url = Column(String(2048), nullable=False, unique=True)
    title = Column(Text, nullable=False)
    short_description = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    image_url = Column(String(2048), nullable=True)
    language = Column(SAEnum(LanguageEnum), nullable=False)
    published_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_published = Column(Boolean, default=True, nullable=False)

    source = relationship("Source", back_populates="articles")
    categories = relationship("ArticleCategory", back_populates="article", cascade="all, delete-orphan")
    tags = relationship("ArticleTag", back_populates="article", cascade="all, delete-orphan")
    locations = relationship("ArticleLocation", back_populates="article", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# M2M: ARTICLE ↔ CATEGORY
# ---------------------------------------------------------------------------

class ArticleCategory(Base):
    __tablename__ = "scraped_article_categories"

    article_id = Column(UUID(as_uuid=True), ForeignKey("scraped_articles.id", ondelete="CASCADE"), primary_key=True)
    category_id = Column(Integer, ForeignKey("scraper_categories.id", ondelete="CASCADE"), primary_key=True)

    article = relationship("Article", back_populates="categories")
    category = relationship("Category", back_populates="articles")


# ---------------------------------------------------------------------------
# M2M: ARTICLE ↔ TAG
# ---------------------------------------------------------------------------

class ArticleTag(Base):
    __tablename__ = "scraped_article_tags"

    article_id = Column(UUID(as_uuid=True), ForeignKey("scraped_articles.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("scraper_tags.id", ondelete="CASCADE"), primary_key=True)

    article = relationship("Article", back_populates="tags")
    tag = relationship("Tag", back_populates="articles")


# ---------------------------------------------------------------------------
# M2M: ARTICLE ↔ LOCATION
# ---------------------------------------------------------------------------

class ArticleLocation(Base):
    __tablename__ = "scraped_article_locations"

    article_id = Column(UUID(as_uuid=True), ForeignKey("scraped_articles.id", ondelete="CASCADE"), primary_key=True)
    location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)

    article = relationship("Article", back_populates="locations")
    location = relationship("Location", back_populates="articles")


# ---------------------------------------------------------------------------
# FETCH RUN LOGS
# ---------------------------------------------------------------------------

class FetchRunLog(Base):
    __tablename__ = "fetch_run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(SAEnum(RunStatusEnum), nullable=True)
    fetcher_used = Column(SAEnum(FetcherUsedEnum), nullable=True)
    urls_found = Column(Integer, default=0)
    articles_saved = Column(Integer, default=0)
    duplicates_skipped = Column(Integer, default=0)
    errors_skipped = Column(Integer, default=0)
    error_detail = Column(Text, nullable=True)

    source = relationship("Source", back_populates="fetch_run_logs")
