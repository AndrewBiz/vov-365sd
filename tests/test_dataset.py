"""Тесты валидатора и сборки на синтетическом наборе данных.

Каждый тест строит временный корень проекта с минимальным валидным набором
и ломает в нём ровно одно правило, чтобы проверить, что нарушение ловится.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import build as builder  # noqa: E402
import validate as vov  # noqa: E402


def _write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _fact(value, source_id="src-test", status="черновик", slide=1):
    return {
        "value": value,
        "provenance": {"source_id": source_id, "locator": {"slide": slide, "quote": "цитата"}},
        "status": status,
    }


def _event(event_id="ev-1941-test-event", **overrides):
    event = {
        "id": event_id,
        "type": "combat",
        "title": _fact("Тестовое событие"),
        "units": ["test-div"],
        "period": {
            "value": {"from": "1941-12-24", "to": "1942-01-10", "precision": "range"},
            "provenance": {"source_id": "src-test", "locator": {"slide": 2}},
            "status": "черновик",
        },
        "localization": "approximate",
    }
    event.update(overrides)
    return event


class DatasetCase(unittest.TestCase):
    """Временный корень проекта с валидным набором и фальшивым Obsidian."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="vov-365sd-test-")
        self.root = os.path.join(self.tmp, "repo")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.root)
        os.makedirs(self.vault)

        shutil.copytree(os.path.join(REPO, "data/schemas"), os.path.join(self.root, "data/schemas"))
        shutil.copy(os.path.join(REPO, "data/registry.json"), os.path.join(self.root, "data/registry.json"))

        original = os.path.join(self.vault, "тема/Исходники/скан.pdf")
        os.makedirs(os.path.dirname(original))
        with open(original, "wb") as fh:
            fh.write(b"test scan")
        derivative = os.path.join(self.root, "docs/sources/недат_Тест.md")
        os.makedirs(os.path.dirname(derivative))
        with open(derivative, "w", encoding="utf-8") as fh:
            fh.write("---\nисточник: тест\n---\n")

        _write(
            os.path.join(self.root, "data/sources.json"),
            {
                "sources": [
                    {
                        "id": "src-test",
                        "kind": "other",
                        "title": "Тестовый источник",
                        "derivative_path": "docs/sources/недат_Тест.md",
                        "files": [{"path": "тема/Исходники/скан.pdf",
                                   "sha256": hashlib.sha256(b"test scan").hexdigest(),
                                   "role": "original"}],
                        "received": "2026-11-25",
                    },
                    {
                        "id": "src-web-test",
                        "kind": "website",
                        "title": "Тестовый сайт",
                        "derivative_path": "docs/sources/недат_Веб — тест.md",
                        "cache_path": "data/cache/test-site",
                        "snapshot_at": "2026-11-25",
                        "files": [{"path": "тема/Исходники/скан.pdf",
                                   "sha256": hashlib.sha256(b"test scan").hexdigest(),
                                   "role": "scans"}],
                        "received": "2026-11-25",
                    },
                    {
                        "id": "src-web-copy",
                        "kind": "other",
                        "title": "Перепечатка тестового сайта",
                        "derivative_path": "docs/sources/недат_Тест.md",
                        "derived_from": ["src-web-test"],
                        "files": [{"path": "тема/Исходники/скан.pdf",
                                   "sha256": hashlib.sha256(b"test scan").hexdigest(),
                                   "role": "original"}],
                        "received": "2026-11-25",
                    },
                    {
                        "id": "src-osm-test",
                        "kind": "gazetteer",
                        "title": "Тестовый газеттир",
                        "derivative_path": "docs/sources/недат_Геокодирование — тест.md",
                        "cache_path": "data/cache/geocode",
                        "received": "2026-11-25",
                    },
                ]
            },
        )
        # геоисточник, журнал и кэш
        os.makedirs(os.path.join(self.root, "data/cache/geocode"), exist_ok=True)
        _write(
            os.path.join(self.root, "data/cache/geocode/test-place.json"),
            {"place_id": "pl-test-place", "queries": ["Тестовое место"], "attempts": [], "accepted_attempt": 0},
        )
        journal = os.path.join(self.root, "docs/sources/недат_Геокодирование — тест.md")
        with open(journal, "w", encoding="utf-8") as fh:
            fh.write("# Журнал\n\n## geo-test-place — Тестовое место\n\n- запрос: `Тестовое место`\n")

        _write(os.path.join(self.root, "data/documents.json"), {"documents": []})
        _write(os.path.join(self.root, "data/people.json"), {"people": []})
        _write(os.path.join(self.root, "data/rosters.json"), {"entries": []})
        _write(os.path.join(self.root, "data/front_lines/1941.json"), {"front_lines": []})

        _write(
            os.path.join(self.root, "data/places.json"),
            {
                "places": [
                    {
                        "id": "pl-test-place",
                        "type": "village",
                        "names": {"modern": "Тестовое место"},
                        "state": "found",
                        "accuracy": "settlement",
                        "region": "Московская область",
                        "coords": {
                            "value": {"lon": 36.5, "lat": 56.3},
                            "provenance": {
                                "source_id": "src-osm-test",
                                "locator": {"paragraph": "geo-test-place"},
                            },
                            "status": "черновик",
                        },
                    },
                    {
                        "id": "pl-test-lost",
                        "type": "village",
                        "names": {"modern": "Потерянное место"},
                        "state": "unfound",
                        "accuracy": "approximate",
                        "region": "Московская область",
                        "coords": None,
                    },
                ]
            },
        )

        # веб-источник со снимком
        site_dir = os.path.join(self.root, "data/cache/test-site/2026-11-25")
        os.makedirs(os.path.join(site_dir, "pages"), exist_ok=True)
        with open(os.path.join(site_dir, "pages/statya.txt"), "w", encoding="utf-8") as fh:
            fh.write("https://site.test/statya/\nтекст страницы\n")
        _write(os.path.join(site_dir, "pages.json"), {"count": 1, "pages": {"https://site.test/statya/": "pages/statya.txt"}})
        with open(os.path.join(self.root, "docs/sources/недат_Веб — тест.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nисточник: тест\n---\n")

        _write(
            os.path.join(self.root, "data/units.json"),
            {
                "units": [
                    {
                        "id": "test-div",
                        "kind": "division",
                        "name": _fact("Тестовая дивизия"),
                    }
                ]
            },
        )
        _write(
            os.path.join(self.root, "data/events/1941.json"),
            {"year": 1941, "events": [_event()]},
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- помощники ------------------------------------------------------
    def load(self, rel: str) -> dict:
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, rel: str, payload: dict) -> None:
        _write(os.path.join(self.root, rel), payload)

    def errors(self) -> list[str]:
        return vov.run(self.root, self.vault)

    def assert_error(self, needle: str) -> None:
        errors = self.errors()
        self.assertTrue(errors, "ожидались нарушения, их нет")
        self.assertTrue(
            any(needle in err for err in errors),
            f"нет нарушения с фрагментом «{needle}»; получено: {errors}",
        )


class TestValidDataset(DatasetCase):
    def test_valid_dataset_has_no_errors(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_registry_and_schemas_load(self) -> None:
        ds = vov.Dataset(self.root, self.vault)
        self.assertIn("source-gap", ds.registry_types["event_types"])


class TestProvenance(DatasetCase):
    def test_fact_without_provenance(self) -> None:
        payload = self.load("data/events/1941.json")
        del payload["events"][0]["title"]["provenance"]
        self.save("data/events/1941.json", payload)
        self.assert_error("без провенанса")

    def test_unknown_source_id(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"]["provenance"]["source_id"] = "src-nope"
        self.save("data/events/1941.json", payload)
        self.assert_error("неизвестный источник")

    def test_lost_locator(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"]["provenance"]["locator"] = {}
        self.save("data/events/1941.json", payload)
        self.assert_error("locator")

    def test_verified_with_single_source(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"]["status"] = "выверено"
        self.save("data/events/1941.json", payload)
        self.assert_error("требует двух независимых")

    def test_two_locators_of_one_source_are_not_independent(self) -> None:
        payload = self.load("data/events/1941.json")
        title = payload["events"][0]["title"]
        title["status"] = "выверено"
        title["provenance"] = [
            {"source_id": "src-test", "locator": {"slide": 1}},
            {"source_id": "src-test", "locator": {"slide": 2}},
        ]
        self.save("data/events/1941.json", payload)
        # два локатора одного источника — не два независимых источника
        self.assert_error("требует двух независимых")

    def test_broken_sha256(self) -> None:
        payload = self.load("data/sources.json")
        payload["sources"][0]["files"][0]["sha256"] = "0" * 64
        self.save("data/sources.json", payload)
        self.assert_error("sha256")

    def test_missing_derivative(self) -> None:
        payload = self.load("data/sources.json")
        payload["sources"][0]["derivative_path"] = "docs/sources/нет-такого.md"
        self.save("data/sources.json", payload)
        self.assert_error("производная")

    def test_missing_original_in_vault(self) -> None:
        os.remove(os.path.join(self.vault, "тема/Исходники/скан.pdf"))
        self.assert_error("не найден в Obsidian")


class TestConflicts(DatasetCase):
    def test_conflict_with_single_variant_rejected(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"] = {
            "conflict": True,
            "variants": [_fact("единственный вариант")],
        }
        self.save("data/events/1941.json", payload)
        self.assert_error("схема event")

    def test_conflict_with_two_variants_allowed(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"] = {
            "conflict": True,
            "variants": [_fact("вариант А"), _fact("вариант Б", slide=3)],
        }
        self.save("data/events/1941.json", payload)
        self.assertEqual(self.errors(), [])

    def test_conflict_variant_cannot_be_verified(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["title"] = {
            "conflict": True,
            "variants": [_fact("вариант А", status="выверено"), _fact("вариант Б", slide=3)],
        }
        self.save("data/events/1941.json", payload)
        self.assert_error("вариант конфликта не может быть")


class TestEvents(DatasetCase):
    def test_duplicate_event_id(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"].append(_event())
        self.save("data/events/1941.json", payload)
        self.assert_error("уже встречался")

    def test_unknown_unit(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["units"] = ["nope-div"]
        self.save("data/events/1941.json", payload)
        self.assert_error("неизвестное формирование")

    def test_unknown_event_type(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["type"] = "teleport"
        self.save("data/events/1941.json", payload)
        self.assert_error("неизвестный тип события")

    def test_geometry_without_localization_level(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["localization"] = "settlement"
        self.save("data/events/1941.json", payload)
        self.assert_error("approximate")

    def test_geometry_present_is_accepted(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["geometry"] = {"type": "Point", "coordinates": [35.6, 56.3]}
        payload["events"][0]["localization"] = "settlement"
        self.save("data/events/1941.json", payload)
        self.assertEqual(self.errors(), [])

    def test_reversed_period(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["period"]["value"].update({"from": "1942-01-10", "to": "1941-12-24"})
        self.save("data/events/1941.json", payload)
        self.assert_error("позже конца")

    def test_month_precision_must_cover_whole_month(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["period"]["value"] = {
            "from": "1942-08-02",
            "to": "1942-08-31",
            "precision": "month",
        }
        self.save("data/events/1941.json", payload)
        self.assert_error("точность 'month'")

    def test_year_precision_must_cover_whole_year(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["period"]["value"] = {
            "from": "1941-01-01",
            "to": "1941-11-30",
            "precision": "year",
        }
        self.save("data/events/1941.json", payload)
        self.assert_error("точность 'year'")

    def test_year_mismatch_with_filename(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["year"] = 1943
        self.save("data/events/1941.json", payload)
        self.assert_error("не совпадает с именем файла")


class TestPlaces(DatasetCase):
    def _coords(self, **overrides) -> dict:
        coords = {
            "value": {"lon": 36.5, "lat": 56.3},
            "provenance": {"source_id": "src-osm-test", "locator": {"paragraph": "geo-test-place"}},
            "status": "черновик",
        }
        coords.update(overrides)
        return coords

    def load_places(self) -> dict:
        return self.load("data/places.json")

    def test_valid_places_have_no_errors(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_coordinates_out_of_range(self) -> None:
        payload = self.load_places()
        payload["places"][0]["coords"]["value"] = {"lon": 200.0, "lat": 56.3}
        self.save("data/places.json", payload)
        self.assert_error("вне диапазона")

    def test_swapped_lat_lon_caught_by_bbox(self) -> None:
        payload = self.load_places()
        payload["places"][0]["coords"]["value"] = {"lon": 56.3, "lat": 36.5}
        self.save("data/places.json", payload)
        self.assert_error("вне ожидаемого района")

    def test_found_place_without_coords(self) -> None:
        payload = self.load_places()
        payload["places"][0]["coords"] = None
        self.save("data/places.json", payload)
        self.assert_error("координат нет")

    def test_unfound_place_with_coords(self) -> None:
        payload = self.load_places()
        payload["places"][1]["coords"] = self._coords()
        self.save("data/places.json", payload)
        self.assert_error("но координаты заданы")

    def test_coords_without_journal_entry(self) -> None:
        payload = self.load_places()
        payload["places"][0]["coords"]["provenance"]["locator"]["paragraph"] = "geo-unknown"
        self.save("data/places.json", payload)
        self.assert_error("нет записи")

    def test_coords_without_geo_locator(self) -> None:
        payload = self.load_places()
        payload["places"][0]["coords"]["provenance"]["locator"] = {"page": 5}
        self.save("data/places.json", payload)
        self.assert_error("запись журнала geo-")

    def test_missing_cache_file(self) -> None:
        os.remove(os.path.join(self.root, "data/cache/geocode/test-place.json"))
        self.assert_error("кэша отсутствует")

    def test_event_referencing_missing_place(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["place"] = "pl-nope"
        self.save("data/events/1941.json", payload)
        self.assert_error("несуществующее место")


class TestDerivedGeometry(DatasetCase):
    def test_geometry_derived_from_place(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["place"] = "pl-test-place"
        payload["events"][0]["localization"] = "approximate"
        self.save("data/events/1941.json", payload)
        code, messages = builder.build(self.root, self.vault)
        self.assertEqual(code, 0, messages)
        with open(os.path.join(self.root, builder.GEOJSON_PATH), encoding="utf-8") as fh:
            geo = json.load(fh)
        self.assertEqual(len(geo["features"]), 1)
        feature = geo["features"][0]
        self.assertEqual(feature["geometry"]["coordinates"], [36.5, 56.3])
        self.assertEqual(feature["properties"]["localization"], "settlement")
        self.assertEqual(feature["properties"]["place_state"], "found")

    def test_own_geometry_wins_over_place(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["place"] = "pl-test-place"
        payload["events"][0]["geometry"] = {"type": "Point", "coordinates": [35.0, 55.0]}
        payload["events"][0]["localization"] = "exact"
        self.save("data/events/1941.json", payload)
        code, messages = builder.build(self.root, self.vault)
        self.assertEqual(code, 0, messages)
        with open(os.path.join(self.root, builder.GEOJSON_PATH), encoding="utf-8") as fh:
            geo = json.load(fh)
        feature = geo["features"][0]
        self.assertEqual(feature["geometry"]["coordinates"], [35.0, 55.0])
        self.assertEqual(feature["properties"]["localization"], "exact")

    def test_unfound_place_excluded_from_geojson(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["place"] = "pl-test-lost"
        self.save("data/events/1941.json", payload)
        code, messages = builder.build(self.root, self.vault)
        self.assertEqual(code, 0, messages)
        with open(os.path.join(self.root, builder.GEOJSON_PATH), encoding="utf-8") as fh:
            geo = json.load(fh)
        self.assertEqual(geo["features"], [])
        with open(os.path.join(self.root, builder.CHRONO_PATH), encoding="utf-8") as fh:
            self.assertIn("место не локализовано", fh.read())


class TestWebsiteSources(DatasetCase):
    def _event_with_web_fact(self, provenance, status="черновик"):
        payload = self.load("data/events/1941.json")
        payload["events"][0]["facts"] = {
            "web": {"value": "цитата с сайта", "provenance": provenance, "status": status}
        }
        self.save("data/events/1941.json", payload)

    def _web_prov(self, **locator):
        loc = {"url": "https://site.test/statya/"}
        loc.update(locator)
        return {"source_id": "src-web-test", "locator": loc}

    def test_web_fact_with_url_is_valid(self) -> None:
        self._event_with_web_fact(self._web_prov(quote="текст"))
        self.assertEqual(self.errors(), [])

    def test_web_fact_without_url(self) -> None:
        self._event_with_web_fact({"source_id": "src-web-test", "locator": {"quote": "цитата"}})
        self.assert_error("нет поля url")

    def test_url_missing_from_snapshot(self) -> None:
        self._event_with_web_fact(self._web_prov(url="https://site.test/drugoe/"))
        self.assert_error("не найден в снимке")

    def test_site_root_instead_of_page(self) -> None:
        self._event_with_web_fact(self._web_prov(url="https://site.test/"))
        self.assert_error("корень сайта")

    def test_snapshot_dir_missing(self) -> None:
        shutil.rmtree(os.path.join(self.root, "data/cache/test-site"))
        self.assert_error("снимок сайта не найден")

    def test_derived_from_unknown_source(self) -> None:
        payload = self.load("data/sources.json")
        payload["sources"][2]["derived_from"] = ["src-ghost"]
        self.save("data/sources.json", payload)
        self.assert_error("derived_from ссылается на неизвестный")

    def test_verified_blocked_by_derived_pair(self) -> None:
        self._event_with_web_fact([
            self._web_prov(),
            {"source_id": "src-web-copy", "locator": {"page": 1}},
        ], status="выверено")
        self.assert_error("подтверждение одно")

    def test_verified_with_independent_sources(self) -> None:
        self._event_with_web_fact([
            self._web_prov(),
            {"source_id": "src-test", "locator": {"slide": 1}},
        ], status="выверено")
        self.assertEqual(self.errors(), [])


class TestSnapshotTool(DatasetCase):
    def test_write_snapshot_is_deterministic_and_sorted(self) -> None:
        import importlib
        snap = importlib.import_module("snapshot_site")
        pages = {
            "https://site.test/b/": {"text": "Б", "html": None},
            "https://site.test/a/": {"text": "А", "html": "<table><tr><td>1</td></tr></table>"},
        }
        cache = os.path.join(self.root, "data/cache/det")
        snap.write_snapshot(cache, "2026-11-25", pages)
        with open(os.path.join(cache, "2026-11-25/pages.json"), encoding="utf-8") as fh:
            first = fh.read()
        snap.write_snapshot(cache, "2026-11-25", pages)
        with open(os.path.join(cache, "2026-11-25/pages.json"), encoding="utf-8") as fh:
            second = fh.read()
        self.assertEqual(first, second)
        mapping = json.loads(first)["pages"]
        self.assertEqual(list(mapping), sorted(mapping))
        self.assertTrue(mapping["https://site.test/a/"]["html"].endswith(".html"))
        self.assertIsNone(mapping["https://site.test/b/"]["html"])

    def test_classify_removed_splits_unreached_and_gone(self) -> None:
        import importlib
        snap = importlib.import_module("snapshot_site")

        def fake_fetch(url: str) -> str:
            if url.endswith("/gone/"):
                raise RuntimeError("404")
            return "ok"

        unreached, gone = snap.classify_removed(
            ["https://site.test/alive/", "https://site.test/gone/"], fetcher=fake_fetch
        )
        self.assertEqual(unreached, ["https://site.test/alive/"])
        self.assertEqual(gone, ["https://site.test/gone/"])



class TestDocuments(DatasetCase):
    def _doc(self, **overrides) -> dict:
        doc = {
            "id": "doc-test",
            "type": "combat-order",
            "title": _fact("Тестовый приказ"),
            "date": {
                "value": {"from": "1941-12-09", "to": "1941-12-09", "precision": "day"},
                "provenance": {"source_id": "src-web-test", "locator": {"url": "https://site.test/statya/"}},
                "status": "черновик",
            },
            "requisites": {"fund": "3067", "inventory": "1", "file": "6", "sheets": "48"},
            "carrier": {"source_id": "src-web-test", "url": "https://site.test/statya/"},
        }
        doc.update(overrides)
        return doc

    def test_valid_document(self) -> None:
        self.save("data/documents.json", {"documents": [self._doc()]})
        self.assertEqual(self.errors(), [])

    def test_partial_requisites_rejected(self) -> None:
        self.save("data/documents.json", {"documents": [self._doc(requisites={"fund": "3067"})]})
        self.assert_error("схема document")

    def test_requisites_none_needs_note(self) -> None:
        self.save("data/documents.json", {"documents": [self._doc(requisites=None)]})
        self.assert_error("нет примечания")

    def test_requisites_none_with_note_ok(self) -> None:
        doc = self._doc(requisites=None, note="реквизиты не установлены")
        self.save("data/documents.json", {"documents": [doc]})
        self.assertEqual(self.errors(), [])

    def test_unknown_document_type(self) -> None:
        self.save("data/documents.json", {"documents": [self._doc(type="telegram")]})
        self.assert_error("неизвестный тип документа")

    def test_carrier_url_not_in_snapshot(self) -> None:
        doc = self._doc(carrier={"source_id": "src-web-test", "url": "https://site.test/other/"})
        self.save("data/documents.json", {"documents": [doc]})
        self.assert_error("не найден в снимке")

    def test_event_referencing_missing_document(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["documents"] = ["doc-ghost"]
        self.save("data/events/1941.json", payload)
        self.assert_error("несуществующий документ")


class TestPersonnel(DatasetCase):
    def _person(self, pid="p-ivanov-ivan", name="Иванов Иван Иванович", year="неизвестен",
                claims: dict | None = None) -> dict:
        person = {"id": pid, "name": name, "birth_year": year}
        person["claims"] = claims or {"fate": [{**_fact("погиб"), "basis": "publication"}]}
        return person

    def test_duplicate_person(self) -> None:
        self.save("data/people.json", {"people": [
            self._person(),
            self._person(pid="p-ivanov-ivan-2", name="иванов иван иванович"),
        ]})
        self.assert_error("возможный дубль")

    def test_same_name_different_years_allowed(self) -> None:
        self.save("data/people.json", {"people": [
            self._person(year=1901), self._person(pid="p-ivanov-ivan-2", year=1902),
        ]})
        self.assertEqual(self.errors(), [])

    def _entry(self, eid="rec-1", **overrides) -> dict:
        entry = {
            "id": eid,
            "document": "doc-test",
            "person": "p-ivanov-ivan",
            "unit": "test-div",
            "fate": _fact("погиб"),
        }
        entry.update(overrides)
        return entry

    def _base_dataset(self) -> None:
        self.save("data/documents.json", {"documents": [TestDocuments._doc(self)]})
        self.save("data/people.json", {"people": [self._person()]})

    def test_valid_entry(self) -> None:
        self._base_dataset()
        self.save("data/rosters.json", {"entries": [self._entry()]})
        self.assertEqual(self.errors(), [])

    def test_entry_unknown_person(self) -> None:
        self._base_dataset()
        self.save("data/rosters.json", {"entries": [self._entry(person="p-ghost")]})
        self.assert_error("неизвестная персона")

    def test_unknown_fate_value(self) -> None:
        self._base_dataset()
        self.save("data/rosters.json", {"entries": [self._entry(fate=_fact("исчез"))]})
        self.assert_error("неизвестная формулировка судьбы")

    def test_divergence_requires_conflict_flag(self) -> None:
        self._base_dataset()
        entry2 = self._entry(eid="rec-2", fate=_fact("пропал без вести"))
        self.save("data/rosters.json", {"entries": [self._entry(), entry2]})
        self.assert_error("не помечена conflict")

    def test_divergence_with_flag_ok(self) -> None:
        person = {"id": "p-ivanov-ivan", "name": "Иванов Иван Иванович", "birth_year": "неизвестен", "conflict": True}
        self.save("data/documents.json", {"documents": [TestDocuments._doc(self)]})
        self.save("data/people.json", {"people": [person]})
        entry2 = self._entry(eid="rec-2", fate=_fact("пропал без вести"))
        self.save("data/rosters.json", {"entries": [self._entry(), entry2]})
        self.assertEqual(self.errors(), [])

    def test_stale_conflict_flag(self) -> None:
        person = {**self._person(), "conflict": True}
        self.save("data/documents.json", {"documents": [TestDocuments._doc(self)]})
        self.save("data/people.json", {"people": [person]})
        self.save("data/rosters.json", {"entries": [self._entry()]})
        self.assert_error("флаг conflict без расхождений")


class TestPersonalLetters(DatasetCase):
    """Личные письма как вид источника и пункты переписки как ключ утверждений."""

    def _sources(self) -> dict:
        return self.load("data/sources.json")

    def _add_letters(self, vault_path: str, sha: str | None = None) -> None:
        payload = self._sources()
        payload["sources"].append({
            "id": "src-letters-test",
            "kind": "personal-letters",
            "title": "Письма с фронта (тест)",
            "derivative_path": "docs/sources/недат_Письма — тест.md",
            "files": [{"path": vault_path,
                       "sha256": sha or hashlib.sha256(b"letters").hexdigest(),
                       "role": "transcription"}],
            "received": "2026-11-26",
        })
        self.save("data/sources.json", payload)
        with open(os.path.join(self.root, "docs/sources/недат_Письма — тест.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nисточник: тест\n---\n")

    def test_personal_letters_source_accepted(self) -> None:
        os.makedirs(os.path.join(self.vault, "письма"), exist_ok=True)
        with open(os.path.join(self.vault, "письма/l.pdf"), "wb") as fh:
            fh.write(b"letters")
        self._add_letters("письма/l.pdf")
        self.assertEqual(self.errors(), [])

    def test_personal_letters_without_origin_file(self) -> None:
        self._add_letters("письма/нет-такого.pdf")
        self.assert_error("не найден в Obsidian")

    def test_personal_letters_checksum_mismatch(self) -> None:
        os.makedirs(os.path.join(self.vault, "письма"), exist_ok=True)
        with open(os.path.join(self.vault, "письма/l2.pdf"), "wb") as fh:
            fh.write(b"other")
        self._add_letters("письма/l2.pdf", sha=hashlib.sha256(b"letters").hexdigest())
        self.assert_error("sha256 файла не совпадает")

    def test_correspondence_is_not_a_conflict(self) -> None:
        person = {
            "id": "p-ivanov-ivan",
            "name": "Иванов Иван Иванович",
            "birth_year": "неизвестен",
            "claims": {
                "fate": [{**_fact("погиб"), "basis": "publication"}],
                "correspondence": [
                    {**_fact({"address": "Камышлов, п/я 44/7", "from": "1941-08-01",
                              "to": "1941-08-31", "precision": "month"}), "basis": "oral-history"},
                    {**_fact({"address": "Кинешма, п/я 74/29", "from": "1941-11-18",
                              "to": "1941-12-21", "precision": "range"}), "basis": "personal-letter"},
                ],
            },
        }
        self.save("data/people.json", {"people": [person]})
        self.assertEqual(self.errors(), [])

    def test_unknown_correspondence_basis_rejected(self) -> None:
        person = {
            "id": "p-ivanov-ivan",
            "name": "Иванов Иван Иванович",
            "birth_year": "неизвестен",
            "claims": {"correspondence": [
                {**_fact({"address": "Камышлов", "from": "1941-08-01",
                          "to": "1941-08-31", "precision": "month"}), "basis": "letter-ish"},
            ]},
        }
        self.save("data/people.json", {"people": [person]})
        self.assert_error("неизвестный вид свидетельства")


class TestSourceFiles(DatasetCase):
    """Источник из нескольких файлов: роли, потерянные части, дубли путей."""

    def _sources(self) -> dict:
        return self.load("data/sources.json")

    def _write_vault(self, name: str, payload: bytes) -> str:
        os.makedirs(os.path.join(self.vault, "папка"), exist_ok=True)
        with open(os.path.join(self.vault, "папка", name), "wb") as fh:
            fh.write(payload)
        return f"папка/{name}"

    def _add(self, files: list[dict], sid: str = "src-multi") -> None:
        payload = self._sources()
        payload["sources"].append({
            "id": sid,
            "kind": "personal-letters",
            "title": "Подборка из нескольких частей",
            "derivative_path": "docs/sources/недат_Тест.md",
            "files": files,
            "received": "2026-11-26",
        })
        self.save("data/sources.json", payload)

    def test_three_parts_accepted(self) -> None:
        files = [
            {"path": self._write_vault("a.pdf", b"a"), "sha256": hashlib.sha256(b"a").hexdigest(), "role": "transcription"},
            {"path": self._write_vault("b.pdf", b"b"), "sha256": hashlib.sha256(b"b").hexdigest(), "role": "scans"},
            {"path": self._write_vault("c.pdf", b"c"), "sha256": hashlib.sha256(b"c").hexdigest(), "role": "photos"},
        ]
        self._add(files)
        self.assertEqual(self.errors(), [])

    def test_second_part_missing(self) -> None:
        good = self._write_vault("a2.pdf", b"a2")
        files = [
            {"path": good, "sha256": hashlib.sha256(b"a2").hexdigest(), "role": "transcription"},
            {"path": "папка/нет-такой.pdf", "sha256": "0" * 64, "role": "scans"},
        ]
        self._add(files)
        self.assert_error("источник неполон")

    def test_second_part_checksum_mismatch(self) -> None:
        a = self._write_vault("a3.pdf", b"a3")
        b = self._write_vault("b3.pdf", b"b3")
        files = [
            {"path": a, "sha256": hashlib.sha256(b"a3").hexdigest(), "role": "transcription"},
            {"path": b, "sha256": hashlib.sha256(b"not b3").hexdigest(), "role": "scans"},
        ]
        self._add(files)
        self.assert_error("sha256 файла не совпадает")

    def test_duplicate_path_in_one_source(self) -> None:
        a = self._write_vault("a4.pdf", b"a4")
        entry = {"path": a, "sha256": hashlib.sha256(b"a4").hexdigest(), "role": "scans"}
        self._add([entry, dict(entry)])
        self.assert_error("дубль пути")

    def test_unknown_role(self) -> None:
        a = self._write_vault("a5.pdf", b"a5")
        self._add([{"path": a, "sha256": hashlib.sha256(b"a5").hexdigest(), "role": "черновик"}])
        self.assert_error("неизвестная роль файла")

    def test_file_in_two_sources_warns(self) -> None:
        a = self._write_vault("a6.pdf", b"a6")
        entry = {"path": a, "sha256": hashlib.sha256(b"a6").hexdigest(), "role": "scans"}
        self._add([entry], sid="src-multi-1")
        self._add([entry], sid="src-multi-2")
        errors = self.errors()
        self.assertEqual(errors, [])
        self.assertTrue(any("нескольких источниках" in w for w in vov.WARNINGS), vov.WARNINGS)

    def test_source_without_files(self) -> None:
        payload = self._sources()
        payload["sources"].append({
            "id": "src-empty",
            "kind": "book-page",
            "title": "Источник без файлов",
            "derivative_path": "docs/sources/недат_Тест.md",
            "received": "2026-11-26",
        })
        self.save("data/sources.json", payload)
        self.assert_error("'files' is a required property")


class TestPositions(DatasetCase):
    """Положения частей: время, геометрия, запрет расчётных точек в данных."""

    def _pos(self, **overrides) -> dict:
        base = {
            "id": "pos-1941-12-15-2200-1211sp-test",
            "unit": "test-div",
            "as_of": {"date": "1941-12-15", "time": "22:00", "precision": "timestamp"},
            "kind": "cp",
            "place": "pl-test-place",
            "label": "1/1211 сп — Тестовое место",
            "sheet": {"source_id": "src-test", "locator": {"quote": "цитата"}, "colour": None},
            "basis": "text",
            "accuracy_m": 2000,
            "status": "черновик",
        }
        base.update(overrides)
        return base

    def _save(self, positions: list[dict]) -> None:
        self.save("data/positions/1941.json", {"year": 1941, "positions": positions})

    def test_valid_position(self) -> None:
        self._save([self._pos()])
        self.assertEqual(self.errors(), [])

    def test_timestamp_without_time(self) -> None:
        self._save([self._pos(as_of={"date": "1941-12-15", "precision": "timestamp"})])
        self.assert_error("требует время")

    def test_sector_needs_two_points(self) -> None:
        self._save([self._pos(kind="sector",
                              geometry={"type": "LineString", "coordinates": [[36.5, 56.3]]},
                              place=None)])
        self.assert_error("минимум две точки")

    def test_derived_position_forbidden(self) -> None:
        self._save([{**self._pos(), "derived": True}])
        self.assert_error("расчётное положение")

    def test_unknown_unit(self) -> None:
        self._save([self._pos(unit="ghost-div")])
        self.assert_error("неизвестное формирование")

    def test_geometry_outside_bbox(self) -> None:
        self._save([self._pos(kind="cp", geometry={"type": "Point", "coordinates": [10.0, 10.0]},
                              place=None)])
        self.assert_error("вне front_bbox")

    def test_neither_geometry_nor_place(self) -> None:
        self._save([self._pos(place=None)])
        self.assert_error("ни геометрии, ни пункта")


class TestTrajectories(unittest.TestCase):
    """Расчёт промежуточных положений: правдоподобие скорости и разрывы."""

    def setUp(self) -> None:
        import json, pathlib, tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "data/positions").mkdir(parents=True)
        self.json = json
        (self.root / "data/registry.json").write_text(json.dumps({
            "movement_rates": {"infantry": {"label": "Пехота", "km_per_day": 30}},
            "movement_by_unit_kind": {"division": "infantry"},
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "data/units.json").write_text(json.dumps({
            "units": [{"id": "test-div", "kind": "division", "name": "Тестовая дивизия"}]
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "data/places.json").write_text(json.dumps({"places": []}, ensure_ascii=False),
                                                    encoding="utf-8")
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import trajectories
        self.traj = trajectories

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, positions: list[dict]) -> None:
        (self.root / "data/positions/1941.json").write_text(
            self.json.dumps({"year": 1941, "positions": positions}, ensure_ascii=False), encoding="utf-8")

    def _pos(self, pid: str, date: str, time: str, lon: float, lat: float) -> dict:
        return {"id": pid, "unit": "test-div",
                "as_of": {"date": date, "time": time, "precision": "timestamp"},
                "kind": "cp", "label": "пункт",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "sheet": {"source_id": "src-x", "locator": {"quote": "ц"}},
                "basis": "text", "accuracy_m": 2000, "status": "черновик"}

    def test_plausible_transition_is_interpolated(self) -> None:
        # ~22 км за 48 ч = 11 км/сут, пехота успевает
        self._write([self._pos("pos-a", "1941-12-10", "09:00", 36.50, 56.30),
                     self._pos("pos-b", "1941-12-12", "09:00", 36.50, 56.50)])
        collection, warnings = self.traj.build(self.root, 6, 10, None)
        derived = [f for f in collection["features"] if f["properties"].get("derived")]
        self.assertTrue(derived, warnings)
        self.assertTrue(all(f["properties"]["between"] == ["pos-a", "pos-b"] for f in derived))
        self.assertFalse([w for w in warnings if "неправдоподобно" in w])

    def test_fast_transition_is_refused(self) -> None:
        # ~110 км за 8 ч — пехотой невозможно
        self._write([self._pos("pos-a", "1941-12-10", "09:00", 36.50, 56.30),
                     self._pos("pos-b", "1941-12-10", "17:00", 38.20, 56.30)])
        collection, warnings = self.traj.build(self.root, 6, 10, None)
        self.assertFalse([f for f in collection["features"] if f["properties"].get("derived")])
        self.assertTrue(any("неправдоподобно" in w for w in warnings), warnings)

    def test_long_gap_is_not_filled(self) -> None:
        self._write([self._pos("pos-a", "1941-12-01", "09:00", 36.50, 56.30),
                     self._pos("pos-b", "1941-12-21", "09:00", 36.60, 56.35)])
        collection, warnings = self.traj.build(self.root, 6, 10, None)
        self.assertFalse([f for f in collection["features"] if f["properties"].get("derived")])
        self.assertTrue(any("разрыв" in w for w in warnings), warnings)


class TestRosterParsing(DatasetCase):
    HTML = """<table>
      <tr><th>№ пп</th><th>Фамилия, имя, отчество</th><th>Партийность</th><th>Состав</th></tr>
      <tr><td>1</td><td>Костин Петр Афонасьевич</td><td>б/п</td><td>МКС</td></tr>
      <tr><td>2</td><td></td><td></td><td></td></tr>
      <tr><td>3</td><td>Попов Иван Павлович</td><td>ВКПб</td><td></td></tr>
    </table>"""

    def test_parse_rows_and_report(self) -> None:
        import importlib, tempfile
        pr = importlib.import_module("parse_roster")
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(self.HTML)
            path = fh.name
        draft = pr.build_draft(path, "https://site.test/list/", "doc-test", "test-div")
        self.assertEqual(draft["report"]["rows_total"], 3)
        self.assertEqual(draft["report"]["rows_kept"], 2)
        self.assertEqual(draft["report"]["rows_without_name"], 1)
        self.assertEqual(draft["rows"][1]["name"], "Попов Иван Павлович")
        os.unlink(path)

    def test_apply_is_deduplicating(self) -> None:
        import importlib, tempfile
        pr = importlib.import_module("parse_roster")
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(self.HTML)
            path = fh.name
        draft = pr.build_draft(path, "https://site.test/list/", "doc-test", "test-div")
        os.unlink(path)
        persons, entries = pr.apply_draft(self.root, draft, "https://site.test/list/", "src-web-test", "test")
        self.assertEqual((persons, entries), (2, 2))
        persons2, entries2 = pr.apply_draft(self.root, draft, "https://site.test/list/", "src-web-test", "test")
        self.assertEqual((persons2, entries2), (0, 0))


class TestPersonEvidence(DatasetCase):
    def _claim(self, **overrides) -> dict:
        claim = {
            "value": "радист",
            "provenance": {"source_id": "src-test", "locator": {"page": 96, "quote": "Радист."}},
            "status": "черновик",
            "basis": "publication",
        }
        claim.update(overrides)
        return claim

    def _person(self, claims=None, **overrides) -> dict:
        person = {
            "id": "p-dyatkov-konstantin",
            "name": "Дятков Константин Александрович",
            "birth_year": 1913,
            "claims": claims if claims is not None else {"speciality": [self._claim()]},
        }
        person.update(overrides)
        return person

    def test_valid_claims(self) -> None:
        self.save("data/people.json", {"people": [self._person()]})
        self.assertEqual(self.errors(), [])

    def test_claim_without_basis(self) -> None:
        claim = self._claim()
        claim.pop("basis")
        self.save("data/people.json", {"people": [self._person({"speciality": [claim]})]})
        self.assert_error("без вида свидетельства")

    def test_unknown_basis(self) -> None:
        self.save("data/people.json", {"people": [self._person({"speciality": [self._claim(basis="слухи")]})]})
        self.assert_error("неизвестный вид свидетельства")

    def test_unknown_claim_key(self) -> None:
        self.save("data/people.json", {"people": [self._person({"favourite_color": [self._claim()]})]})
        self.assert_error("неизвестное имя утверждения")

    def test_claim_without_provenance(self) -> None:
        claim = self._claim()
        claim.pop("provenance")
        self.save("data/people.json", {"people": [self._person({"speciality": [claim]})]})
        self.assert_error("без провенанса")

    def test_empty_person(self) -> None:
        person = {"id": "p-empty-person", "name": "Пустая Персона", "birth_year": "неизвестен"}
        self.save("data/people.json", {"people": [person]})
        self.assert_error("персона пуста")

    def test_claims_divergence_needs_flag(self) -> None:
        claims = {"speciality": [self._claim(), self._claim(value="связист")]}
        self.save("data/people.json", {"people": [self._person(claims)]})
        self.assert_error("не помечена conflict")

    def test_claims_divergence_with_flag_ok(self) -> None:
        claims = {"speciality": [self._claim(), self._claim(value="связист")]}
        self.save("data/people.json", {"people": [self._person(claims, conflict=True)]})
        self.assertEqual(self.errors(), [])

    def test_publication_requisites_ok(self) -> None:
        doc = TestDocuments._doc(self)
        doc["requisites"] = {"edition": "Книга Памяти Свердловской области", "volume": "16", "page": "202", "entry": "№ 12"}
        doc["type"] = "memorial-card"
        self.save("data/documents.json", {"documents": [doc]})
        self.assertEqual(self.errors(), [])

    def test_mixed_requisites_rejected(self) -> None:
        doc = TestDocuments._doc(self)
        doc["requisites"] = {"fund": "3067", "inventory": "1", "file": "6", "edition": "Книга Памяти"}
        self.save("data/documents.json", {"documents": [doc]})
        self.assert_error("схема document")

    def test_publication_without_page(self) -> None:
        doc = TestDocuments._doc(self)
        doc["requisites"] = {"edition": "Книга Памяти", "volume": "16"}
        self.save("data/documents.json", {"documents": [doc]})
        self.assert_error("схема document")


class TestPortalIntake(DatasetCase):
    PORTAL = {
        "id": "src-portal-test",
        "kind": "archive-portal",
        "title": "Тестовый портал",
        "derivative_path": "docs/sources/недат_Тест.md",
        "cache_path": "data/cache/portal-test",
        "portal": "https://portal.test/documents/",
        "fetched_at": "2026-11-25",
        "query": {"division": "365сд"},
        "received": "2026-11-25",
    }

    def _portal(self, files=None, catalogue=True) -> str:
        sources = self.load("data/sources.json")
        sources["sources"].append(dict(self.PORTAL))
        self.save("data/sources.json", sources)
        cache = os.path.join(self.root, "data/cache/portal-test")
        os.makedirs(cache, exist_ok=True)
        if catalogue:
            _write(os.path.join(cache, "catalogue.json"), {"files": files or []})
        return cache

    def _leaf(self, size: int = 11, name: str = "передача/208-0002511-0275/p00000003.jpg") -> str:
        path = os.path.join(self.vault, "Хобби/365сд/Исходники/ЦАМО — Память народа", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        return "Хобби/365сд/Исходники/ЦАМО — Память народа/" + name

    def _portal_doc(self, **overrides) -> dict:
        doc = TestDocuments._doc(self)
        doc["id"] = "doc-pn-208-0002511-0275-p00000003"
        doc["type"] = "map"
        doc["carrier"] = {
            "source_id": "src-portal-test",
            "portal_url": "https://portal.test/documents/view/?id=map1",
            "portal_id": "map1",
            "file_path": self._leaf(),
            "bytes": 11,
            "page": 3,
        }
        doc.update(overrides)
        return doc

    def test_portal_source_with_catalogue(self) -> None:
        self._portal()
        self.assertEqual(self.errors(), [])

    def test_portal_source_without_catalogue(self) -> None:
        self._portal(catalogue=False)
        self.assert_error("нет catalogue.json")

    def test_portal_source_without_query(self) -> None:
        sources = self.load("data/sources.json")
        portal = dict(self.PORTAL); portal.pop("query")
        sources["sources"].append(portal)
        self.save("data/sources.json", sources)
        cache = os.path.join(self.root, "data/cache/portal-test")
        os.makedirs(cache, exist_ok=True)
        _write(os.path.join(cache, "catalogue.json"), {"files": []})
        self.assert_error("запрос отбора")

    def test_portal_document_valid(self) -> None:
        self._portal([{"file": "передача/208-0002511-0275/p00000003.jpg", "bytes": 11,
                       "sha256": hashlib.sha256(b"x" * 11).hexdigest()}])
        self.save("data/documents.json", {"documents": [self._portal_doc()]})
        self.assertEqual(self.errors(), [])

    def test_portal_document_without_card(self) -> None:
        self._portal()
        carrier = dict(self._portal_doc()["carrier"]); carrier.pop("portal_url")
        self.save("data/documents.json", {"documents": [self._portal_doc(carrier=carrier)]})
        self.assert_error("нет ссылки на карточку портала")

    def test_portal_document_without_file(self) -> None:
        self._portal()
        carrier = dict(self._portal_doc()["carrier"]); carrier.pop("file_path")
        self.save("data/documents.json", {"documents": [self._portal_doc(carrier=carrier)]})
        self.assert_error("нет пути к файлу листа")

    def test_portal_document_missing_leaf(self) -> None:
        self._portal([{"file": "нет/такого/файла.jpg", "bytes": 11}])
        carrier = dict(self._portal_doc()["carrier"])
        carrier["file_path"] = "Хобби/365сд/Исходники/ЦАМО — Память народа/нет/такого/файла.jpg"
        self.save("data/documents.json", {"documents": [self._portal_doc(carrier=carrier)]})
        self.assert_error("не найден в хранилище")

    def test_portal_document_size_mismatch(self) -> None:
        self._portal([{"file": "передача/208-0002511-0275/p00000003.jpg", "bytes": 99}])
        self.save("data/documents.json", {"documents": [self._portal_doc()]})
        self.assert_error("не совпадает с каталогом")

    def test_deep_hash_mismatch(self) -> None:
        self._portal([{"file": "передача/208-0002511-0275/p00000003.jpg", "bytes": 11,
                       "sha256": "0" * 64}])
        self.save("data/documents.json", {"documents": [self._portal_doc()]})
        errors = vov.run(self.root, self.vault, deep=True)
        self.assertTrue(any("sha256" in e for e in errors), errors)

    def test_intake_is_idempotent_and_rejects_unknown_type(self) -> None:
        import importlib
        pi = importlib.import_module("portal_intake")
        entry = {
            "id": "map1", "type": "Карты", "name": "Карта", "deal": "208-0002511-0275",
            "page": "00000003", "period_from": "05.12.1941", "period_to": "06.12.1941",
            "doc_date": "06.12.1941", "file": "передача/208-0002511-0275/p00000003.jpg",
            "bytes": 11, "sha256": "0" * 64, "portal_url": "https://portal.test/documents/view/?id=map1",
        }
        reg = self.load("data/registry.json")
        doc = pi.build_document(entry, "src-portal-test", "prefix/", reg["portal_types"])
        self.assertEqual(doc["id"], "doc-pn-208-0002511-0275-p00000003")
        self.assertEqual(doc["type"], "map")
        self.assertEqual(doc["requisites"], {"fund": "208", "inventory": "2511", "file": "275"})
        self.assertIn("выведены из пути портала", doc["note"])
        bad = dict(entry); bad["type"] = "Неизвестный тип"
        with self.assertRaises(SystemExit):
            pi.build_document(bad, "src-portal-test", "prefix/", reg["portal_types"])


class TestFrontLines(DatasetCase):
    def _line(self, **overrides) -> dict:
        line = {
            "id": "fl-1941-12-06",
            "date": {
                "value": {"from": "1941-12-06", "to": "1941-12-06", "precision": "day"},
                "provenance": {"source_id": "src-test", "locator": {"slide": 1, "quote": "6.12.41"}},
                "status": "черновик",
            },
            "geometry": {"type": "LineString",
                         "coordinates": [[36.7, 56.3], [36.9, 56.4], [37.1, 56.2]]},
            "accuracy_km": 10,
            "accuracy_level": "sheet-1m",
            "provenance": {"source_id": "src-web-test",
                           "locator": {"sheet_date": "1941-12-06", "sheet": "ca000124"}},
            "note": "тест",
        }
        line.update(overrides)
        return line

    def test_valid_line(self) -> None:
        self.save("data/front_lines/1941.json", {"front_lines": [self._line()]})
        self.assertEqual(self.errors(), [])

    def test_date_mismatch_with_sheet(self) -> None:
        line = self._line()
        line["provenance"]["locator"]["sheet_date"] = "1941-12-05"
        self.save("data/front_lines/1941.json", {"front_lines": [line]})
        self.assert_error("не совпадает с датой листа")

    def test_sheet_1m_cannot_be_precise(self) -> None:
        self.save("data/front_lines/1941.json", {"front_lines": [self._line(accuracy_km=2)]})
        self.assert_error("точности лучше ±5 км")

    def test_unknown_accuracy_level(self) -> None:
        self.save("data/front_lines/1941.json", {"front_lines": [self._line(accuracy_level="на глаз")]})
        self.assert_error("неизвестный уровень точности")

    def test_point_outside_theatre(self) -> None:
        line = self._line()
        line["geometry"]["coordinates"] = [[36.7, 56.3], [10.0, 45.0]]
        self.save("data/front_lines/1941.json", {"front_lines": [line]})
        self.assert_error("за границами театра")

    def test_duplicate_points(self) -> None:
        line = self._line()
        line["geometry"]["coordinates"] = [[36.7, 56.3], [36.7, 56.3], [37.0, 56.2]]
        self.save("data/front_lines/1941.json", {"front_lines": [line]})
        self.assert_error("дублируются")

    def test_self_intersection(self) -> None:
        line = self._line()
        line["geometry"]["coordinates"] = [[36.6, 56.2], [37.0, 56.4], [37.0, 56.2], [36.6, 56.4]]
        self.save("data/front_lines/1941.json", {"front_lines": [line]})
        self.assert_error("самопересекается")

    def test_warning_when_event_far_from_line(self) -> None:
        payload = self.load("data/events/1941.json")
        payload["events"][0]["period"]["value"] = {"from": "1941-12-06", "to": "1941-12-06", "precision": "day"}
        payload["events"][0]["place"] = "pl-far"
        self.save("data/events/1941.json", payload)
        places = self.load("data/places.json")
        places["places"].append({
            "id": "pl-far", "type": "village", "names": {"modern": "Далёкое"},
            "state": "found", "accuracy": "settlement", "region": "Тверская область",
            "coords": {"value": {"lon": 40.5, "lat": 58.5},
                       "provenance": {"source_id": "src-test", "locator": {"slide": 1}},
                       "status": "черновик"},
        })
        self.save("data/places.json", places)
        self.save("data/front_lines/1941.json", {"front_lines": [self._line()]})
        self.assertEqual(self.errors(), [])
        self.assertTrue(any("от линии" in w for w in vov.WARNINGS), vov.WARNINGS)

    def test_affine_recovers_known_transform(self) -> None:
        import importlib, math
        ft = importlib.import_module("front_line_trace")
        lon0, lat0 = 36.0, 56.0
        kx = math.cos(math.radians(lat0)) * ft.M_PER_DEG_LON_EQ

        def truth(px, py):
            east = 0.9 * px + 0.08 * py - 400
            north = -0.06 * px - 0.92 * py + 3000
            return lon0 + east / kx, lat0 + north / ft.M_PER_DEG_LAT

        calib = [{"x": px, "y": py, "lon": round(truth(px, py)[0], 5),
                  "lat": round(truth(px, py)[1], 5), "label": f"p{i}"}
                 for i, (px, py) in enumerate([(1200, 1600), (1400, 900), (900, 1450), (1900, 1900)])]
        coords, stats = ft.affine([{"x": 1200, "y": 1600}, {"x": 1300, "y": 1500}], calib)
        self.assertLess(stats["rms_m"], 50)
        expected = truth(1300, 1500)
        self.assertAlmostEqual(coords[1][0], round(expected[0], 5), places=3)
        self.assertAlmostEqual(coords[1][1], round(expected[1], 5), places=3)

    def test_affine_rejects_collinear_calibration(self) -> None:
        import importlib
        ft = importlib.import_module("front_line_trace")
        bad = [{"x": 0, "y": 0, "lon": 36.0, "lat": 56.0},
               {"x": 100, "y": 100, "lon": 36.1, "lat": 56.1},
               {"x": 200, "y": 200, "lon": 36.2, "lat": 56.2}]
        with self.assertRaises(SystemExit):
            ft.affine([{"x": 0, "y": 0}, {"x": 10, "y": 10}], bad)

    def test_trace_tool_maps_calibration_points(self) -> None:
        import importlib
        ft = importlib.import_module("front_line_trace")
        a = {"x": 100, "y": 100, "lon": 36.0, "lat": 56.0}
        b = {"x": 600, "y": 100, "lon": 37.0, "lat": 56.0}
        coords, residual = ft.similarity([{"x": 100, "y": 100}, {"x": 350, "y": 100}, {"x": 600, "y": 100}], a, b)
        self.assertLess(residual, 1.0)
        self.assertAlmostEqual(coords[0][0], 36.0, places=2)
        self.assertAlmostEqual(coords[2][0], 37.0, places=2)
        self.assertAlmostEqual(coords[1][0], 36.5, places=1)


class TestAiSteps(unittest.TestCase):
    """Шаги с моделью: скилы, разбор ответа, проверка цитат и мест, применение."""

    def setUp(self) -> None:
        import json
        import pathlib
        import shutil
        import sys
        import tempfile
        import types

        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        real = pathlib.Path(__file__).resolve().parent.parent
        (self.root / "data").mkdir()
        shutil.copytree(real / "data/schemas", self.root / "data/schemas")
        shutil.copytree(real / "data/events", self.root / "data/events")
        shutil.copytree(real / "data/front_lines", self.root / "data/front_lines")
        shutil.copytree(real / "tools/skills", self.root / "tools/skills")
        shutil.copytree(real / "docs/sources", self.root / "docs/sources")
        (self.root / "data/intake").mkdir()
        for name in ("sources.json", "documents.json", "registry.json", "places.json",
                     "units.json", "people.json", "rosters.json"):
            shutil.copy2(real / "data" / name, self.root / "data" / name)
        (self.root / "inbox" / "probe" / "files").mkdir(parents=True)
        (self.root / "inbox" / "probe" / "files" / "leaf.jpg").write_bytes(b"\xff\xd8\xff\xe0x")
        sys.path.insert(0, str(real / "tools"))
        import ai_steps
        import validate
        self.ai = ai_steps
        self.vov = validate
        self.json = json
        self.types = types
        self._original_run = validate.run

    def tearDown(self) -> None:
        if getattr(self, "_original_run", None) is not None:
            self.vov.run = self._original_run
        self.tmp.cleanup()

    def write_manifest(self, **overrides) -> None:
        import yaml
        manifest = {"slug": "probe", "kind": "archive-document", "title": "Проба",
                    "source_id": "src-pamyat-naroda", "received": "2026-11-26",
                    "requisites": {"fund": "354", "inventory": "5806", "file": "10", "sheet": "295"},
                    "date": {"from": "1941-12-19", "to": "1941-12-19", "precision": "day"},
                    "unit": "365 сд", "files": ["files/leaf.jpg"],
                    "original_location": "Тест/конвейер", "document_type": "operational-summary"}
        manifest.update(overrides)
        (self.root / "inbox" / "probe" / "artifact.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")

    def test_unknown_skill_lists_known(self) -> None:
        with self.assertRaises(self.ai.AiStepError) as ctx:
            self.ai.load_skill(self.root, "classify-magic")
        self.assertIn("extract-facts", str(ctx.exception))

    def test_skill_requires_output_schema(self) -> None:
        (self.root / "tools/skills/broken.yaml").write_text(
            "name: broken\ninput: {kind: text}\nprompt: x\n", encoding="utf-8")
        with self.assertRaises(self.ai.AiStepError) as ctx:
            self.ai.load_skill(self.root, "broken")
        self.assertIn("output", str(ctx.exception))

    def test_skill_name_must_match_file(self) -> None:
        (self.root / "tools/skills/mismatch.yaml").write_text(
            "name: other\ninput: {kind: text}\noutput: {type: object}\nprompt: x\n", encoding="utf-8")
        with self.assertRaises(self.ai.AiStepError):
            self.ai.load_skill(self.root, "mismatch")

    def test_response_schema_is_enforced(self) -> None:
        skill = self.ai.load_skill(self.root, "extract-facts")
        problems = self.ai.validate_output(self.root, skill, {"items": [{"kind": "combat"}]})
        self.assertTrue(problems)

    def test_json_in_fence_is_parsed(self) -> None:
        payload = self.ai.load_json_loose('```json\n{"items": []}\n```')
        self.assertEqual(payload, {"items": []})

    def test_quote_must_exist_in_text(self) -> None:
        self.write_manifest()
        skill = self.ai.load_skill(self.root, "extract-facts")
        text = "части дивизии сосредоточились в районе ВОЛОВНИКОВО"
        payload = {"items": [{"kind": "movement", "summary": "сосредоточение в Воловниково",
                              "quote": "выдуманная цитата", "confidence": 0.9,
                              "date_from": "1941-12-19", "precision": "day"}]}
        checked = self.ai.check_items(self.root, skill, payload, text)
        self.assertFalse(checked[0]["acceptable"])
        self.assertIn("цитата не найдена", " ".join(checked[0]["problems"]))

    def test_unknown_place_is_rejected(self) -> None:
        self.write_manifest()
        skill = self.ai.load_skill(self.root, "extract-facts")
        text = "сосредоточились в районе ВОЛОВНИКОВО"
        payload = {"items": [{"kind": "movement", "summary": "сосредоточение",
                              "quote": "сосредоточились в районе ВОЛОВНИКОВО",
                              "confidence": 0.9, "date_from": "1941-12-19",
                              "precision": "day", "place_name": "Атлантида"}]}
        checked = self.ai.check_items(self.root, skill, payload, text)
        self.assertFalse(checked[0]["acceptable"])
        self.assertIn("нет в справочнике", " ".join(checked[0]["problems"]))

    def test_low_confidence_is_flagged(self) -> None:
        self.write_manifest()
        skill = self.ai.load_skill(self.root, "extract-facts")
        text = "противник отошёл"
        payload = {"items": [{"kind": "combat", "summary": "противник отошёл",
                              "quote": "противник отошёл", "confidence": 0.3,
                              "date_from": "1941-12-19", "precision": "day"}]}
        checked = self.ai.check_items(self.root, skill, payload, text)
        self.assertIn("низкая уверенность", " ".join(checked[0]["problems"]))

    def test_apply_proposal_is_idempotent(self) -> None:
        self.write_manifest()
        items = {"payload": {"items": []}, "checked": [], "skill": "extract-facts", "model": "test"}
        text = "части дивизии овладели ДЯТЛОВО"
        items["checked"] = [{"n": 1, "acceptable": True, "place_id": None, "problems": [],
                             "item": {"kind": "combat", "event_type": "combat",
                                      "date_from": "1941-12-18", "date_to": None, "precision": "day",
                                      "units": ["365sd"], "place_name": None,
                                      "summary": "полк овладел Дятлово",
                                      "quote": text, "confidence": 0.9}}]
        target = self.root / "data/proposals/probe"
        target.mkdir(parents=True)
        (target / "extract-facts.json").write_text(
            self.json.dumps(items, ensure_ascii=False), encoding="utf-8")
        args = self.types.SimpleNamespace(root=str(self.root), slug="probe", step="extract-facts",
                                          min_confidence=0.7, dry_run=False)
        self.vov.run = lambda root, vault: []   # проверяем логику применения, а не валидатор

        def count() -> int:
            return len(self.json.loads((self.root / "data/events/1941.json").read_text(encoding="utf-8"))["events"])

        start = count()
        self.assertEqual(self.ai.cmd_apply(args), 0)
        self.assertEqual(count(), start + 1, "применение должно добавить ровно одно событие")
        self.ai.cmd_apply(args)
        self.assertEqual(count(), start + 1, "повторное применение не должно добавлять события")

    def test_apply_proposal_rolls_back_on_validation_error(self) -> None:
        self.write_manifest()
        items = {"payload": {"items": []}, "skill": "extract-facts", "model": "test", "checked": [
            {"n": 1, "acceptable": True, "place_id": None, "problems": [],
             "item": {"kind": "combat", "event_type": "combat", "date_from": "1941-12-18",
                      "date_to": None, "precision": "day", "units": ["365sd"], "place_name": None,
                      "summary": "полк овладел Дятлово", "quote": "части дивизии овладели ДЯТЛОВО",
                      "confidence": 0.9}}]}
        target = self.root / "data/proposals/probe"
        target.mkdir(parents=True)
        (target / "extract-facts.json").write_text(self.json.dumps(items, ensure_ascii=False), encoding="utf-8")
        before = (self.root / "data/events/1941.json").read_text(encoding="utf-8")
        self.vov.run = lambda root, vault: ["сломанное правило"]
        args = self.types.SimpleNamespace(root=str(self.root), slug="probe", step="extract-facts",
                                          min_confidence=0.7, dry_run=False)
        self.assertEqual(self.ai.cmd_apply(args), 1)
        self.assertEqual(before, (self.root / "data/events/1941.json").read_text(encoding="utf-8"),
                         "при ошибке валидатора файл событий должен вернуться как был")

    def test_offline_propose_writes_request_bundle(self) -> None:
        self.write_manifest()
        cache = self.root / "data/cache/ocr"
        cache.mkdir(parents=True)
        (cache / "doc-pn-354-0005806-0010-p00000295_psm6_x2.txt").write_text(
            "части дивизии сосредоточились в районе ВОЛОВНИКОВО", encoding="utf-8")
        args = self.types.SimpleNamespace(root=str(self.root), slug="probe", step="extract-facts",
                                          emit_request=True, ingest=None, model_name=None)
        self.assertEqual(self.ai.cmd_propose(args), 0)
        bundle = self.root / "data/proposals/probe/extract-facts-request.json"
        self.assertTrue(bundle.exists())
        data = self.json.loads(bundle.read_text(encoding="utf-8"))
        self.assertEqual(data["skill"], "extract-facts")
        self.assertIn("prompt_sha", data)


class TestIntake(unittest.TestCase):
    """Входной конвейер: манифест, журнал, состояния, дедуп, dry-run применения."""

    def setUp(self) -> None:
        import json
        import pathlib
        import shutil
        import sys
        import tempfile
        import yaml

        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        real = pathlib.Path(__file__).resolve().parent.parent
        (self.root / "data").mkdir()
        shutil.copytree(real / "data/schemas", self.root / "data/schemas")
        for name in ("sources.json", "documents.json", "registry.json"):
            shutil.copy2(real / "data" / name, self.root / "data" / name)
        (self.root / "inbox").mkdir()
        (self.root / "data/intake").mkdir(exist_ok=True)
        sys.path.insert(0, str(real / "tools"))
        import intake
        self.intake = intake
        self.yaml = yaml
        self.json = json

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_artifact(self, slug: str = "probe", **overrides) -> pathlib.Path:
        folder = self.root / "inbox" / slug
        (folder / "files").mkdir(parents=True, exist_ok=True)
        (folder / "files" / "leaf.jpg").write_bytes(b"\xff\xd8\xff\xe0probe")
        manifest = {
            "slug": slug, "kind": "archive-document", "title": "Проба",
            "source_id": "src-pamyat-naroda", "received": "2026-11-26",
            "origin": {"portal_url": "https://pamyat-naroda.ru/documents/view/?id=document1"},
            "requisites": {"fund": "354", "inventory": "5806", "file": "10", "sheet": "777"},
            "date": {"from": "1941-12-16", "to": "1941-12-16", "precision": "day"},
            "unit": "365 сд", "files": ["files/leaf.jpg"],
            "original_location": "Тест/конвейер", "document_type": "operational-summary",
        }
        manifest.update(overrides)
        (folder / "artifact.yaml").write_text(self.yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
        return folder

    def test_manifest_requires_files(self) -> None:
        folder = self.make_artifact()
        (folder / "files" / "leaf.jpg").unlink()
        manifest = self.intake.read_manifest(self.root, "probe")
        problems = self.intake.validate_manifest(self.root, manifest, "probe")
        self.assertTrue(any("не найден" in p for p in problems), problems)

    def test_manifest_rejects_unknown_kind_and_source(self) -> None:
        self.make_artifact(kind="telepatiya", source_id="src-nope")
        manifest = self.intake.read_manifest(self.root, "probe")
        problems = self.intake.validate_manifest(self.root, manifest, "probe")
        self.assertTrue(any("неизвестный вид" in p for p in problems), problems)
        self.assertTrue(any("не зарегистрирован" in p for p in problems), problems)

    def test_manifest_rejects_partial_requisites(self) -> None:
        self.make_artifact(requisites={"fund": "354", "file": "10"})
        manifest = self.intake.read_manifest(self.root, "probe")
        problems = self.intake.validate_manifest(self.root, manifest, "probe")
        self.assertTrue(any("частичные" in p for p in problems), problems)

    def test_manifest_accepts_null_requisites(self) -> None:
        self.make_artifact(requisites=None)
        manifest = self.intake.read_manifest(self.root, "probe")
        self.assertEqual(self.intake.validate_manifest(self.root, manifest, "probe"), [])

    def test_states_follow_journal(self) -> None:
        self.assertEqual(self.intake.state_of(self.root, "probe"), "new")
        self.intake.journal_append(self.root, "probe", "scan", "ok")
        self.assertEqual(self.intake.state_of(self.root, "probe"), "proposed")
        self.intake.journal_append(self.root, "probe", "apply", "applied", document="doc-x")
        self.assertEqual(self.intake.state_of(self.root, "probe"), "applied")

    def test_reject_wins_over_scan(self) -> None:
        self.intake.journal_append(self.root, "probe", "scan", "ok")
        self.intake.journal_append(self.root, "probe", "reject", "rejected", reason="не тот лист")
        self.assertEqual(self.intake.state_of(self.root, "probe"), "rejected")

    def test_journal_reports_broken_line(self) -> None:
        path = self.root / "data/intake/journal.jsonl"
        path.write_text('{"slug": "ok"}\n{битый json}\n', encoding="utf-8")
        with self.assertRaises(self.intake.IntakeError) as ctx:
            self.intake.journal_read(self.root)
        self.assertIn(":2", str(ctx.exception))

    def test_document_id_from_requisites(self) -> None:
        manifest = {"slug": "probe", "source_id": "src-pamyat-naroda",
                    "requisites": {"fund": "354", "inventory": "5806", "file": "10", "sheet": "251"}}
        self.assertEqual(self.intake.derive_document_id(manifest), "doc-pn-354-0005806-0010-p00000251")

    def test_document_id_falls_back_to_slug(self) -> None:
        manifest = {"slug": "book-p96", "source_id": "src-book-p96", "requisites": None}
        self.assertEqual(self.intake.derive_document_id(manifest), "doc-book-p96")

    def test_duplicate_by_hash(self) -> None:
        documents = [d for d in self.intake.existing_documents(self.root) if (d.get("carrier") or {}).get("sha256")]
        self.assertTrue(documents, "в фикстуре нет документов с хешем")
        donor = documents[0]
        files = [{"sha256": donor["carrier"]["sha256"], "name": "leaf.jpg"}]
        manifest = {"slug": "probe", "source_id": "src-pamyat-naroda", "requisites": None}
        found = self.intake.find_duplicates(self.root, manifest, files)
        self.assertTrue(any(d["by"] == "sha256" and d["document"] == donor["id"] for d in found), found)

    def test_suggested_document_fits_schema(self) -> None:
        self.make_artifact()
        manifest = self.intake.read_manifest(self.root, "probe")
        files = [{"sha256": "a" * 64, "bytes": 12}]
        record = self.intake.suggest_document(manifest, files)
        self.assertEqual(record["id"], "doc-pn-354-0005806-0010-p00000777")
        self.assertEqual(self.intake.validate_record(self.root, record), [])

    def test_import_does_not_touch_network(self) -> None:
        source = (pathlib.Path(__file__).resolve().parent.parent / "tools/intake.py").read_text(encoding="utf-8")
        for forbidden in ("requests.", "urllib.request.urlopen(", "http.client."):
            self.assertNotIn(forbidden, source, f"офлайн-шаг не должен ходить в сеть: {forbidden}")

    # --- входящие в хранилище -------------------------------------------------
    def make_vault(self) -> pathlib.Path:
        vault = self.root / "vault"
        (vault / self.intake.VAULT_INBOX).mkdir(parents=True, exist_ok=True)
        return vault

    def put_in_vault(self, vault: pathlib.Path, name: str, payload: bytes) -> str:
        target = vault / self.intake.VAULT_INBOX / name
        target.write_bytes(payload)
        return str(target.relative_to(vault))

    def test_vault_inbox_lists_new_and_known(self) -> None:
        vault = self.make_vault()
        new_rel = self.put_in_vault(vault, "scan.pdf", b"%PDF-1.4 new")
        known = self.json.loads((self.root / "data/sources.json").read_text(encoding="utf-8"))["sources"][0]["files"][0]
        # известный файл: кладём в входящие копию с тем же содержимым, что и у источника
        known_path = pathlib.Path(self.intake.vault_dir(None)) / known["path"]
        payload = known_path.read_bytes() if known_path.exists() else b"known"
        known_rel = self.put_in_vault(vault, "known.bin", payload)
        (vault / self.intake.VAULT_INBOX / "README.md").write_text("инструкция", encoding="utf-8")
        args = type("A", (), {"root": str(self.root), "vault": str(vault), "path": None, "json": True})()
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.intake.cmd_inbox(args)
        rows = {r["path"]: r for r in self.json.loads(buffer.getvalue())}
        self.assertIn(new_rel, rows)
        self.assertNotIn(f"{self.intake.VAULT_INBOX}/README.md", rows, "инструкция не считается файлом для разбора")
        self.assertEqual(rows[new_rel]["known"], [])
        self.assertIn(known_rel, rows)
        if rows[known_rel]["known"]:
            self.assertTrue(rows[known_rel]["known"][0].startswith("src-"))

    def test_adopt_keeps_file_in_vault(self) -> None:
        vault = self.make_vault()
        rel = self.put_in_vault(vault, "podborka.pdf", b"%PDF-1.4 letters")
        args = type("A", (), {"root": str(self.root), "vault": str(vault), "file": rel,
                              "slug": "letters-probe", "kind": "personal-letters",
                              "title": "Проба писем", "source": None, "source_id_new": "src-letters-probe",
                              "role": "transcription", "location": "Письма (проба)",
                              "derivative": "docs/sources/недат_Проба.md", "comment": None,
                              "path": None, "received": "2026-11-26", "force": False})()
        self.intake.cmd_adopt(args)
        manifest = self.intake.read_manifest(self.root, "letters-probe")
        self.assertTrue(manifest["from_vault"])
        self.assertEqual(manifest["files"][0]["path"], rel)
        self.assertTrue((vault / rel).exists(), "файл не должен исчезать из хранилища при adopt")
        self.assertFalse((self.root / "inbox" / "letters-probe" / "files").exists())

    def test_source_apply_moves_file_and_registers(self) -> None:
        vault = self.make_vault()
        rel = self.put_in_vault(vault, "letters-2.pdf", b"%PDF-1.4 part two")
        (self.root / "docs/sources").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/sources/недат_Проба.md").write_text("---\nисточник: проба\n---\n", encoding="utf-8")
        adopt = type("A", (), {"root": str(self.root), "vault": str(vault), "file": rel,
                               "slug": "l2", "kind": "personal-letters", "title": "Часть вторая",
                               "source": None, "source_id_new": "src-l2", "role": "scans",
                               "location": "Письма (проба)", "derivative": "docs/sources/недат_Проба.md",
                               "comment": None, "path": None, "received": "2026-11-26", "force": False})()
        self.intake.cmd_adopt(adopt)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.intake.scan_one(self.root, "l2")
        apply_args = type("A", (), {"root": str(self.root), "slug": "l2", "vault": str(vault),
                                    "dry_run": False, "force": False, "source_id": None})()
        self.intake.cmd_apply(apply_args)
        sources = self.json.loads((self.root / "data/sources.json").read_text(encoding="utf-8"))["sources"]
        created = next(s for s in sources if s["id"] == "src-l2")
        target = vault / created["files"][0]["path"]
        self.assertTrue(target.exists(), "файл должен оказаться в Исходники")
        self.assertFalse((vault / rel).exists(), "файл должен уйти из входящих")
        self.assertEqual(self.intake.validate_source_record(self.root, created), [])

    def test_scan_without_ocr_skips_readability(self) -> None:
        vault = self.make_vault()
        rel = self.put_in_vault(vault, "handwritten.tif", b"II*\x00 handwritten")
        (self.root / "docs/sources").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/sources/недат_Проба.md").write_text("---\nисточник: проба\n---\n", encoding="utf-8")
        adopt = type("A", (), {"root": str(self.root), "vault": str(vault), "file": rel,
                               "slug": "hand", "kind": "personal-letters", "title": "Рукописные сканы",
                               "source": None, "source_id_new": "src-hand", "role": "scans",
                               "location": "Письма (проба)", "derivative": "docs/sources/недат_Проба.md",
                               "comment": None, "path": None, "received": "2026-11-26", "force": False})()
        self.intake.cmd_adopt(adopt)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.intake.scan_one(self.root, "hand", ocr=False)
        self.assertEqual(code, 0)
        extraction = self.json.loads((self.root / "data/proposals/hand/extraction.json").read_text(encoding="utf-8"))
        self.assertIn("skipped", extraction["files"][0]["ocr"])

    def test_source_apply_rolls_back_on_schema_error(self) -> None:
        vault = self.make_vault()
        rel = self.put_in_vault(vault, "bad.pdf", b"%PDF-1.4 bad")
        adopt = type("A", (), {"root": str(self.root), "vault": str(vault), "file": rel,
                               "slug": "bad", "kind": "personal-letters", "title": "Плохая запись",
                               "source": None, "source_id_new": "src-bad", "role": "scans",
                               "location": "Письма (проба)", "derivative": None,
                               "comment": None, "path": None, "received": "2026-11-26", "force": False})()
        self.intake.cmd_adopt(adopt)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.intake.scan_one(self.root, "bad")
        self.assertEqual(code, 1, "без derivative_path разбор должен отказывать")
        self.assertTrue((vault / rel).exists())


class TestCoreVersion(unittest.TestCase):
    """Правило проекта: у ядра есть версия, и она меняется вместе с изменением инструмента."""

    def setUp(self) -> None:
        import sys
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

    def test_version_is_semver_and_documented(self) -> None:
        import coremeta
        value = coremeta.version()
        self.assertRegex(value, r"^\d+\.\d+\.\d+$")
        self.assertTrue(coremeta.changelog_has(value), f"в CHANGELOG нет записи для {value}")

    def test_version_rejects_bad_format(self) -> None:
        import coremeta, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "VERSION"
            bad.write_text("v1.0\n", encoding="utf-8")
            with self.assertRaises(coremeta.CoreVersionError):
                coremeta.version(bad)

    def test_version_rejects_missing_file(self) -> None:
        import coremeta, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(coremeta.CoreVersionError):
                coremeta.version(pathlib.Path(tmp) / "VERSION")

    def test_guard_refuses_core_without_version(self) -> None:
        import guard_core
        code, message = guard_core.decide(["tools/validate.py"])
        self.assertEqual(code, 1)
        self.assertIn("tools/VERSION", message)

    def test_guard_blocks_core_directly_on_master(self) -> None:
        import guard_core
        self.assertIsNotNone(guard_core.branch_violation("master", ["tools/validate.py"]))
        self.assertIsNotNone(guard_core.branch_violation(
            "master", ["tools/validate.py", "tools/VERSION", "tools/CHANGELOG.md"]))
        # только версия и история — не файлы ядра, коммит не запрещён
        self.assertIsNone(guard_core.branch_violation("master", ["tools/VERSION"]))
        self.assertIsNone(guard_core.branch_violation("feature/x", ["tools/validate.py"]))
        self.assertIsNone(guard_core.branch_violation("master", ["data/events/1941.json"]))

    def test_guard_allows_release_commit(self) -> None:
        import guard_core
        code, message = guard_core.decide(["tools/validate.py", "tools/VERSION", "tools/CHANGELOG.md"])
        self.assertEqual(code, 0)
        self.assertIn("релизный", message)

    def test_guard_allows_content_only(self) -> None:
        import guard_core
        code, message = guard_core.decide(["data/events/1941.json", "openspec/specs/core-versioning/spec.md"])
        self.assertEqual(code, 0)
        self.assertIn("контент", message)

    def test_bump_levels(self) -> None:
        import release_core
        self.assertEqual(release_core.bump("1.2.3", "patch"), "1.2.4")
        self.assertEqual(release_core.bump("1.2.3", "minor"), "1.3.0")
        self.assertEqual(release_core.bump("1.2.3", "major"), "2.0.0")

    def test_changelog_entry_goes_on_top(self) -> None:
        import release_core
        text = "# История версий ядра\n\nшапка\n\n## 1.0.0 — 2026-11-26\n\n- старое\n"
        updated = release_core.insert_entry(text, "1.0.1", "2026-12-01")
        self.assertLess(updated.index("## 1.0.1"), updated.index("## 1.0.0"))
        self.assertIn("2026-12-01", updated)


class TestBuild(DatasetCase):
    def test_build_writes_artifacts(self) -> None:
        code, messages = builder.build(self.root, self.vault)
        self.assertEqual(code, 0, messages)
        chrono = os.path.join(self.root, builder.CHRONO_PATH)
        geojson = os.path.join(self.root, builder.GEOJSON_PATH)
        self.assertTrue(os.path.exists(chrono))
        self.assertTrue(os.path.exists(geojson))
        with open(chrono, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("СГЕНЕРИРОВАНО", text)
        self.assertIn("Тестовое событие", text)

    def test_build_is_idempotent(self) -> None:
        builder.build(self.root, self.vault)
        first = {}
        for rel in (builder.CHRONO_PATH, builder.GEOJSON_PATH):
            with open(os.path.join(self.root, rel), "rb") as fh:
                first[rel] = hashlib.sha256(fh.read()).hexdigest()
        builder.build(self.root, self.vault)
        for rel in (builder.CHRONO_PATH, builder.GEOJSON_PATH):
            with open(os.path.join(self.root, rel), "rb") as fh:
                self.assertEqual(first[rel], hashlib.sha256(fh.read()).hexdigest(), rel)

    def test_build_blocked_by_invalid_data(self) -> None:
        payload = self.load("data/events/1941.json")
        del payload["events"][0]["title"]["provenance"]
        self.save("data/events/1941.json", payload)
        code, messages = builder.build(self.root, self.vault)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "data/dist")), "dist не должен появляться")
        self.assertFalse(os.path.exists(os.path.join(self.root, builder.CHRONO_PATH)))
        self.assertTrue(any("ОШИБКА" in msg for msg in messages))

    def test_check_mode_detects_stale_artifact(self) -> None:
        builder.build(self.root, self.vault)
        chrono = os.path.join(self.root, builder.CHRONO_PATH)
        with open(chrono, "a", encoding="utf-8") as fh:
            fh.write("\nправка руками\n")
        code, messages = builder.build(self.root, self.vault, check=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("РАСХОЖДЕНИЕ" in msg for msg in messages))


if __name__ == "__main__":
    unittest.main(verbosity=2)
