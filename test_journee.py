import copy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
from test_portionneur import app, FILE


class DayCalculations(unittest.TestCase):
    def recipe(self):
        return {"total_cal": 2000, "ingredients": [{"nom": "Plat de test", "poids": 1500,
                "cal": 2000, "prot": 100, "gluc": 250, "lip": 50, "fibres": 20,
                "nutrition_verified": True}]}

    def test_distribution_exact_with_and_without_snack(self):
        for snack in (True, False):
            parts = app.meal_targets(app.DAY_DEFAULTS, snack)
            self.assertEqual(len(parts), 4 if snack else 3)
            for key in app.DAY_DEFAULTS:
                self.assertEqual(sum(row[key] for row in parts.values()), Decimal(str(app.DAY_DEFAULTS[key])))
            self.assertEqual("Collation" in parts, snack)
        tiny = {key: 0.2 for key in app.DAY_DEFAULTS}
        parts = app.meal_targets(tiny, True)
        for key in tiny:
            self.assertEqual(sum(row[key] for row in parts.values()), Decimal("0.2"))
            self.assertTrue(all(row[key] >= 0 for row in parts.values()))

    def test_targets_are_adjustable_and_not_implicitly_changed(self):
        targets = {**app.DAY_DEFAULTS, "kcal": 2100, "prot": 90}
        before = copy.deepcopy(targets)
        app.meal_targets(targets, False)
        self.assertEqual(targets, before)

    def test_plan_does_not_count_as_eaten(self):
        planned = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        self.assertEqual(app.day_totals([planned])["kcal"], 600)
        self.assertEqual(app.day_totals([])["kcal"], 0)
        self.assertEqual(app.day_balance(app.DAY_DEFAULTS, [])["kcal"], 2400)

    def test_actual_quantity_scales_calories_and_macros(self):
        planned = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        self.assertEqual(planned["poids"], 450)
        events = app.record_consumption([], planned, 225)
        totals = app.day_totals(events)
        self.assertEqual(totals["kcal"], 300)
        self.assertEqual(totals["prot"], 15)
        self.assertEqual(totals["gluc"], Decimal("37.5"))
        self.assertEqual(app.day_balance(app.DAY_DEFAULTS, events)["kcal"], 2100)
        self.assertEqual(planned["poids"], 450)

    def test_double_click_no_double_count(self):
        planned = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        once = app.record_consumption([], planned)
        twice = app.record_consumption(once, planned)
        self.assertEqual(twice, once)
        self.assertEqual(len(twice), 1)

    def test_cancel_and_record_again(self):
        planned = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        events = app.record_consumption([], planned)
        events[0]["annule"] = True
        self.assertEqual(app.day_totals(events)["kcal"], 0)
        repeated = app.record_consumption(events, planned)
        self.assertEqual(len(repeated), 2)
        self.assertEqual(app.day_totals(repeated)["kcal"], 600)

    def test_unknown_macros_not_reported_as_zero(self):
        planned = app.entry_from_recipe("Plat", {"total_cal": 2000}, 1500, 600, "Déjeuner")
        events = app.record_consumption([], planned)
        self.assertEqual(app.day_totals(events)["kcal"], 600)
        self.assertIsNone(app.day_totals(events)["prot"])
        self.assertIsNone(app.day_balance(app.DAY_DEFAULTS, events)["prot"])

    def test_over_goal_signed_balance_and_no_compensation(self):
        food = app.entry_from_food("Aliment", {"kcal": 900, "lip": 100}, 300, "Dîner")
        balance = app.day_balance(app.DAY_DEFAULTS, [food])
        self.assertEqual(balance["kcal"], -300)
        self.assertEqual(app.DAY_DEFAULTS["kcal"], 2400)

    def test_legacy_weekly_planning_preserved(self):
        old = {"planning": {"Lundi": {"Midi": {"recette": "Pizza", "cible": 600}}}}
        changed = app.change_plan(old, "2026-10-29", [])
        self.assertEqual(changed["Lundi"], old["planning"]["Lundi"])
        self.assertNotIn(app.DAY_CONFIG_KEY, old["planning"])

    def test_recipe_change_does_not_rewrite_saved_plan(self):
        recipe = self.recipe()
        plan = app.entry_from_recipe("Plat", recipe, 1500, 600, "Déjeuner")
        recipe["ingredients"][0]["cal"] = 9999
        self.assertEqual(plan["kcal"], 600)

    def test_invalid_actual_weight_and_meal(self):
        plan = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        for weight in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                app.record_consumption([], plan, weight)
        with self.assertRaises(ValueError):
            app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Inconnu")

    def test_backup_roundtrip_preserves_daily_and_legacy_data(self):
        plan = app.entry_from_recipe("Plat", self.recipe(), 1500, 600, "Déjeuner")
        data = {"recettes": {}, "plats": {}, "planning": app.change_plan({}, "2026-10-29", [plan]),
                "journal": {"2026-10-29": app.record_consumption([], plan)}}
        self.assertEqual(app.validate_data(data), data)
        duplicated = copy.deepcopy(data)
        duplicated["planning"][app.DAY_CONFIG_KEY]["days"]["2026-10-29"].append(copy.deepcopy(plan))
        with self.assertRaises(ValueError):
            app.validate_data(duplicated)


class DayInterface(unittest.TestCase):
    def make_app(self):
        at = AppTest.from_file(str(FILE), default_timeout=30)
        data = {"recettes": {"Plat": DayCalculations().recipe()}, "plats": {},
                "garde_manger": {"Riz": {"kcal": 360, "prot": 7, "gluc": 78, "lip": 1, "fibres": 2}},
                "planning": {}, "journal": {}}
        at.session_state._booted = True
        at.session_state._cloud_ready = False
        at.session_state._data = data
        at.session_state._versions = {key: app.fingerprint(value) for key, value in data.items()}
        at.run()
        self.assertFalse(at.exception)
        return at

    @staticmethod
    def field(at, label):
        return next(item for item in at.number_input if item.label == label)

    @staticmethod
    def button(at, label):
        return next(item for item in at.button if item.label == label)

    def test_plan_consume_cancel_restore_end_to_end(self):
        at = self.make_app()
        self.field(at, "Poids net cuit du plat entier pour ce repas (g)").set_value(1500.0)
        self.button(at, "Prévoir cette portion").click().run()
        self.assertFalse(at.exception)
        day = app.today_paris().isoformat()
        self.assertFalse(at.session_state._data["journal"])
        self.assertEqual(len(app.day_config(at.session_state._data)["days"][day]), 1)
        planned = app.day_config(at.session_state._data)["days"][day][0]
        self.field(at, "Quantité réellement mangée (g)").set_value(planned["poids"] / 2)
        self.button(at, "J’ai mangé cette portion").click().run()
        self.assertFalse(at.exception)
        events = at.session_state._data["journal"][day]
        self.assertAlmostEqual(float(app.day_totals(events)["kcal"]), planned["kcal"] / 2)
        self.assertTrue(self.button(at, "J’ai mangé cette portion").disabled)
        self.button(at, "Annuler l’enregistrement : Plat").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(app.day_totals(at.session_state._data["journal"][day])["kcal"], 0)
        self.button(at, "Restaurer Plat").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state._data["journal"][day]), 1)

    def test_snack_change_and_custom_goal_persist(self):
        at = self.make_app()
        next(item for item in at.checkbox if item.label == "Prévoir une collation facultative").uncheck()
        self.field(at, "Repère calories par jour (kcal)").set_value(2200.0)
        self.button(at, "Enregistrer mes repères").click().run()
        self.assertFalse(at.exception)
        config = app.day_config(at.session_state._data)
        self.assertFalse(config["snack"])
        self.assertEqual(config["targets"]["kcal"], 2200)
        self.assertEqual(next(item for item in at.selectbox if item.label == "Repas à préparer").options,
                         ["Petit déjeuner", "Déjeuner", "Dîner"])

    def test_day_separation_and_future_consumption_disabled(self):
        at = self.make_app()
        tomorrow = app.today_paris() + timedelta(days=1)
        at.date_input(key="day_selected_date").set_value(tomorrow).run()
        self.field(at, "Poids net cuit du plat entier pour ce repas (g)").set_value(1500.0)
        self.button(at, "Prévoir cette portion").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(self.button(at, "J’ai mangé cette portion").disabled)
        self.assertFalse(at.session_state._data["journal"])

    def test_typical_day_requires_all_choices_no_partial_save(self):
        at = self.make_app()
        self.button(at, "Calculer et prévoir cette journée").click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.session_state._data["planning"])
        self.assertTrue(any("Choisissez une recette" in item.value for item in at.error))

    def test_typical_day_all_meals_and_totals(self):
        at = self.make_app()
        for item in at.selectbox:
            if item.label.startswith("Recette pour "):
                item.select("Plat")
        for item in at.number_input:
            if item.label.startswith("Poids net cuit du plat entier — "):
                item.set_value(1500.0)
        self.button(at, "Calculer et prévoir cette journée").click().run()
        self.assertFalse(at.exception)
        plans = app.day_config(at.session_state._data)["days"][app.today_paris().isoformat()]
        self.assertEqual(len(plans), 4)
        self.assertAlmostEqual(float(app.day_totals(plans)["kcal"]), 2400, delta=3)
        self.assertFalse(at.session_state._data["journal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
