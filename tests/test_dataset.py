"""Тесты валидатора и сборки на синтетическом наборе данных.

Каждый тест строит временный корень проекта с минимальным валидным набором
и ломает в нём ровно одно правило, чтобы проверить, что нарушение ловится.
"""
from __future__ import annotations

import hashlib
import json
import os
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
                        "vault_path": "тема/Исходники/скан.pdf",
                        "derivative_path": "docs/sources/недат_Тест.md",
                        "sha256": hashlib.sha256(b"test scan").hexdigest(),
                        "received": "2026-11-25",
                    },
                    {
                        "id": "src-web-test",
                        "kind": "website",
                        "title": "Тестовый сайт",
                        "vault_path": "тема/Исходники/скан.pdf",
                        "derivative_path": "docs/sources/недат_Веб — тест.md",
                        "cache_path": "data/cache/test-site",
                        "snapshot_at": "2026-11-25",
                        "sha256": hashlib.sha256(b"test scan").hexdigest(),
                        "received": "2026-11-25",
                    },
                    {
                        "id": "src-web-copy",
                        "kind": "other",
                        "title": "Перепечатка тестового сайта",
                        "vault_path": "тема/Исходники/скан.pdf",
                        "derivative_path": "docs/sources/недат_Тест.md",
                        "derived_from": ["src-web-test"],
                        "sha256": hashlib.sha256(b"test scan").hexdigest(),
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
        payload["sources"][0]["sha256"] = "0" * 64
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
