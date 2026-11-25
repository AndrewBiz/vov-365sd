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
                    }
                ]
            },
        )
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
