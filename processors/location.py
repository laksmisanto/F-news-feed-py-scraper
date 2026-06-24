"""
Location processor — detects geographic locations in article text.

Rules:
  - Bangla (bn) sources: detect BD locations using geo hierarchy
      City → District → Division (most specific first)
      Store matched location + all parent levels
  - English (en) sources: detect country only using countries.json
  - If no match → skip (no location stored)
"""

import json
import re
import os
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from db.queries import get_or_create_location
from utils.logger import get_logger

logger = get_logger("processor.location")

BD_GEO_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "keywords", "locations_bd.json"
)
COUNTRIES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "keywords", "countries.json"
)


class LocationProcessor:

    def __init__(self):
        self._bd_geo: dict = {}
        self._countries: dict = {}
        self._load()

    def _load(self):
        try:
            with open(BD_GEO_PATH, "r", encoding="utf-8") as f:
                self._bd_geo = json.load(f)
        except Exception as e:
            logger.error(f"[Location] Failed to load locations_bd.json: {e}")

        try:
            with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                self._countries = json.load(f)
        except Exception as e:
            logger.error(f"[Location] Failed to load countries.json: {e}")

    def _contains(self, text: str, keywords: list[str]) -> bool:
        text_lower = text.lower()
        for kw in keywords:
            if kw.lower() in text_lower:
                return True
        return False

    # -----------------------------------------------------------------------
    # BD LOCATION DETECTION
    # -----------------------------------------------------------------------

    def detect_bd(self, text: str) -> list[dict]:
        """
        Detect BD locations in text.
        Returns list of dicts: {city, district, division} — most specific available.
        Each match includes all parent levels for storage.
        """
        if not text or not self._bd_geo:
            return []

        matches = []
        seen_districts = set()

        for division_name, division_data in self._bd_geo.items():
            districts = division_data.get("districts", {})

            for district_name, district_data in districts.items():
                cities = district_data.get("cities", {})

                for city_name, city_keywords in cities.items():
                    if self._contains(text, city_keywords):
                        if district_name not in seen_districts:
                            seen_districts.add(district_name)
                        matches.append({
                            "city": city_name,
                            "district": district_name,
                            "division": division_name,
                        })

                # If no city matched, check district-level
                if district_name not in seen_districts:
                    if self._contains(text, district_data.get("keywords", [])):
                        seen_districts.add(district_name)
                        matches.append({
                            "city": None,
                            "district": district_name,
                            "division": division_name,
                        })

        return matches

    # -----------------------------------------------------------------------
    # INTERNATIONAL COUNTRY DETECTION
    # -----------------------------------------------------------------------

    def detect_country(self, text: str) -> list[dict]:
        """
        Detect international countries in text.
        Returns list of {country_name, country_code}.
        """
        if not text or not self._countries:
            return []

        matches = []
        for country_name, data in self._countries.items():
            if self._contains(text, data.get("keywords", [])):
                matches.append({
                    "country": country_name,
                    "code": data.get("code", ""),
                })

        return matches

    # -----------------------------------------------------------------------
    # RESOLVE TO DB IDs
    # -----------------------------------------------------------------------

    async def resolve(
        self, text: str, language: str, session: AsyncSession
    ) -> list[int]:
        """
        Detect locations and resolve to DB IDs.
        """
        location_ids = []

        if language == "bn":
            bd_matches = self.detect_bd(text)
            for match in bd_matches:
                try:
                    location_ids.extend(
                        await self._save_bd_match(match, session)
                    )
                except Exception as e:
                    logger.warning(f"[Location] BD save error: {e}")
        else:
            country_matches = self.detect_country(text)
            for match in country_matches:
                try:
                    loc = await get_or_create_location(
                        session,
                        name=match["country"],
                        loc_type="country",
                        parent_id=None,
                        country_code=match["code"],
                    )
                    if loc.id not in location_ids:
                        location_ids.append(loc.id)
                except Exception as e:
                    logger.warning(f"[Location] Country save error: {e}")

        return location_ids

    async def _save_bd_match(
        self, match: dict, session: AsyncSession
    ) -> list[int]:
        """
        Save a BD location match — store division, district, and city
        as separate rows with parent references. Return all their IDs.
        """
        ids = []

        # Division
        division = await get_or_create_location(
            session,
            name=match["division"],
            loc_type="division",
            parent_id=None,
            country_code="BD",
        )
        ids.append(division.id)

        if match.get("district"):
            # District → parent is division
            district = await get_or_create_location(
                session,
                name=match["district"],
                loc_type="district",
                parent_id=division.id,
                country_code="BD",
            )
            ids.append(district.id)

            if match.get("city"):
                # City → parent is district
                city = await get_or_create_location(
                    session,
                    name=match["city"],
                    loc_type="city",
                    parent_id=district.id,
                    country_code="BD",
                )
                ids.append(city.id)

        return ids
