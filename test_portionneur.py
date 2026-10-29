import copy
from decimal import Decimal
import importlib.util
from pathlib import Path
import random
import unittest
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest

FILE = Path(__file__).with_name("mon_app.py")
spec = importlib.util.spec_from_file_location("portionneur", FILE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class Calculations(unittest.TestCase):
    def test_reference(self):
        r = app.calculate_portion(2000, 1500, 600)
        self.assertEqual(r["grams"], 450)
        self.assertEqual(r["actual_kcal"], 600)

    def test_tare_and_net_equivalent(self):
        self.assertEqual(app.calculate_portion(2000, 2500, 600, 1000),
                         app.calculate_portion(2000, 1500, 600))

    def test_regression_negative_and_oversized(self):
        for target in (-100, 0, 5000):
            with self.subTest(target=target), self.assertRaises(ValueError):
                app.calculate_portion(1859, 2000, target, 1000)

    def test_invalid_values(self):
        for position in range(4):
            for value in (True, None, "texte", "NaN", "Infinity", "-Infinity", -1):
                args = [2000, 1500, 600, 0]
                args[position] = value
                with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                    app.calculate_portion(*args)

    def test_zero_and_impossible_weight(self):
        for total, weight, target, tare in ((0, 1000, 600, 0), (2000, 0, 600, 0),
                                          (2000, 900, 600, 1000), (2000, 1000, 600, 1000)):
            with self.assertRaises(ValueError):
                app.calculate_portion(total, weight, target, tare)

    def test_french_decimal(self):
        r = app.calculate_portion("2000,5", "1500,5", "600,5")
        self.assertGreater(r["grams"], 0)

    def test_rounding_not_truncation(self):
        self.assertEqual(app.calculate_portion(1000, 1000, "100.5")["grams"], 101)

    def test_whole_dish_and_fractional_available_weight(self):
        r = app.calculate_portion(1000, "1000.6", 1000)
        self.assertLessEqual(r["grams"], r["net"])
        self.assertEqual(r["remaining"], 0)

    def test_subgram_portion(self):
        with self.assertRaises(ValueError):
            app.calculate_portion(1000, 100, 1)

    def test_2000_random_reference_calculations(self):
        rng = random.Random(18)
        for _ in range(2000):
            total = Decimal(rng.randint(100, 20000))
            net = Decimal(rng.randint(100, 10000))
            target = Decimal(rng.randint(1, int(total)))
            exact = net * target / total
            if exact < Decimal("0.5"):
                with self.assertRaises(ValueError):
                    app.calculate_portion(total, net, target)
                continue
            r = app.calculate_portion(total, net, target)
            self.assertLessEqual(abs(r["grams"] - exact), Decimal("0.5"))
            self.assertGreater(r["grams"], 0)
            self.assertLessEqual(r["grams"], net)
            self.assertEqual(r["remaining"] + r["grams"], net)
            self.assertLessEqual(abs(r["actual_kcal"] - target), total / net / 2 + Decimal("1e-20"))


class Recipes(unittest.TestCase):
    def test_per100_and_total_same(self):
        a = app.ingredient_row("Riz", 150, 360, "pour 100 g")
        b = app.ingredient_row("Riz", 150, 540, "pour la quantité utilisée")
        self.assertEqual(a, b)

    def test_no_early_rounding(self):
        rows = [app.ingredient_row("A", 1, 33, "pour 100 g") for _ in range(3)]
        self.assertEqual(app.recipe_total({"ingredients": rows}), Decimal("0.99"))

    def test_total_recomputed_from_ingredients(self):
        self.assertEqual(app.recipe_total({"total_cal": 999, "ingredients": [{"cal": 100}, {"cal": 50}]}), 150)

    def test_invalid_ingredient(self):
        for args in ((" ", 1, 1), ("a", 0, 1), ("a", 1, -1)):
            with self.assertRaises(ValueError):
                app.ingredient_row(*args, "pour 100 g")

    def test_legacy_recipe_and_container(self):
        data = {"recettes": {"Ancienne": {"total_cal": 1500}}, "plats": {"1": 1000}}
        self.assertEqual(app.validate_data(data), data)

    def test_invalid_backups(self):
        for data in ([], {"recettes": []}, {"plats": {"a": -1}},
                     {"recettes": {"a": {"total_cal": 0}}},
                     {"recettes": {"a": {"ingredients": [{"nom": "a", "poids": 0, "cal": 1}]}}}):
            with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                app.validate_data(data)


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class Saving(unittest.TestCase):
    def setUp(self):
        self.original = {"recettes": {}, "plats": {}}
        self.state = State(_data=copy.deepcopy(self.original), _cloud_ready=True,
                           _versions={k: app.fingerprint(v) for k, v in self.original.items()})
        self.ui = MagicMock(session_state=self.state)

    def test_failed_save_keeps_session(self):
        sheet = MagicMock()
        sheet.worksheet.return_value.update_acell.side_effect = OSError("offline")
        with patch.object(app, "st", self.ui), patch.object(app, "read_cloud_key", return_value={}), patch.object(app, "cloud_sheet", return_value=sheet):
            self.assertFalse(app.persist("plats", {"plat": 1000}))
        self.assertEqual(self.state._data, self.original)

    def test_conflict_blocks_overwrite(self):
        sheet = MagicMock()
        with patch.object(app, "st", self.ui), patch.object(app, "read_cloud_key", return_value={"Autre": 100}), patch.object(app, "cloud_sheet", return_value=sheet):
            self.assertFalse(app.persist("plats", {"plat": 1000}))
        sheet.assert_not_called()
        self.assertEqual(self.state._data, self.original)

    def test_success_commits_after_write(self):
        sheet = MagicMock()
        with patch.object(app, "st", self.ui), patch.object(app, "read_cloud_key", return_value={}), patch.object(app, "cloud_sheet", return_value=sheet):
            self.assertTrue(app.persist("plats", {"plat": 1000}))
        self.assertEqual(self.state._data["plats"], {"plat": 1000})


class Interface(unittest.TestCase):
    def make_app(self):
        at = AppTest.from_file(str(FILE), default_timeout=20)
        at.session_state._booted = True
        at.session_state._data = {"recettes": {"Pizza": {"total_cal": 1859}}, "plats": {"Plat": 1000}}
        at.session_state._cloud_ready = False
        at.session_state._versions = {k: app.fingerprint(v) for k, v in at.session_state._data.items()}
        at.run()
        self.assertFalse(at.exception)
        return at

    @staticmethod
    def field(at, label):
        return next(x for x in at.number_input if x.label == label)

    def test_clean_start_without_secrets(self):
        at = AppTest.from_file(str(FILE), default_timeout=20).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Mode local" in x.value for x in at.warning))

    def test_direct_calculation(self):
        at = self.make_app()
        self.field(at, "Calories du plat entier (kcal)").set_value(2000.0)
        self.field(at, "Poids net du plat cuit (g)").set_value(1500.0)
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("450 g" in x.value for x in at.success))

    def test_tare_no_double_subtraction(self):
        at = self.make_app()
        self.field(at, "Calories du plat entier (kcal)").set_value(2000.0)
        at.radio[1].set_value("Plat avec son contenant").run()
        self.field(at, "Tare du contenant (g)").set_value(1000.0)
        self.field(at, "Poids affiché sur la balance (g)").set_value(2500.0)
        at.run()
        self.assertTrue(any("450 g" in x.value for x in at.success))

    def test_zero_and_excess_target_no_result(self):
        at = self.make_app()
        self.field(at, "Calories du plat entier (kcal)").set_value(500.0)
        self.field(at, "Poids net du plat cuit (g)").set_value(1000.0)
        at.run()
        self.assertFalse(at.success)
        self.assertTrue(any("ne contient que" in x.value for x in at.info))
        self.field(at, "Calories de la portion (kcal)").set_value(0.0)
        at.run()
        self.assertFalse(at.success)
        self.assertFalse(at.exception)

    def test_existing_recipe(self):
        at = self.make_app()
        at.radio[0].set_value("Recette enregistrée").run()
        self.field(at, "Poids net du plat cuit (g)").set_value(1000.0)
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("323 g" in x.value for x in at.success))

    def test_create_save_reuse_recipe_end_to_end(self):
        at = self.make_app()
        next(x for x in at.text_input if x.label == "Nom de la recette").set_value("Riz de test")
        next(x for x in at.text_input if x.label == "Nom de l’ingrédient").set_value("Riz cru")
        self.field(at, "Quantité utilisée (g)").set_value(200.0)
        self.field(at, "Calories indiquées (kcal)").set_value(360.0)
        next(x for x in at.button if x.label == "Ajouter l’ingrédient").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state._rows[0]["cal"], 720)
        next(x for x in at.button if x.label == "Enregistrer la recette").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state._data["recettes"]["Riz de test"]["total_cal"], 720)
        at.radio[0].set_value("Recette enregistrée").run()
        next(x for x in at.selectbox if x.label == "Recette").select("Riz de test").run()
        self.field(at, "Poids net du plat cuit (g)").set_value(600.0)
        self.field(at, "Calories de la portion (kcal)").set_value(360.0)
        at.run()
        self.assertTrue(any("300 g" in x.value for x in at.success))

    def test_recipe_editor_switches_without_stale_ingredients(self):
        at = self.make_app()
        next(x for x in at.selectbox if x.label == "Recette à préparer").select("Pizza").run()
        self.assertEqual(at.text_input(key="recipe_name").value, "Pizza")
        next(x for x in at.selectbox if x.label == "Recette à préparer").select("Nouvelle recette").run()
        self.assertEqual(at.text_input(key="recipe_name").value, "")
        self.assertEqual(at.session_state._rows, [])

    def test_container_duplicate_blocked(self):
        at = self.make_app()
        next(x for x in at.text_input if x.label == "Nom du contenant").set_value("Plat")
        self.field(at, "Poids du contenant vide (g)").set_value(2000.0)
        next(x for x in at.button if x.label == "Enregistrer le contenant").click().run()
        self.assertEqual(at.session_state._data["plats"]["Plat"], 1000)
        self.assertTrue(any("existe déjà" in x.value for x in at.error))

    def test_no_recipe_no_crash(self):
        at = self.make_app()
        at.session_state._data = {"recettes": {}, "plats": {}}
        at.radio[0].set_value("Recette enregistrée").run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Aucune recette" in x.value for x in at.info))


if __name__ == "__main__":
    unittest.main(verbosity=2)
