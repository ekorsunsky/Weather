import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shortcut"))
sys.path.insert(0, str(ROOT / "tests"))

import build_shortcut  # noqa: E402
from simulate import SimulationError, control_flow_groups, run  # noqa: E402

NOW = {"Temperature": "58°F", "Condition": "Cloudy"}


def forecast(chance, **today):
    return [
        {"Condition": "Rain", "High": "64°F", "Low": "51°F", "Precipitation Chance": chance, **today},
        {"Condition": "Partly Cloudy", "High": "66°F", "Low": "50°F", "Precipitation Chance": 0.1},
    ]


def say(chance, **options):
    return run(build_shortcut.build(**options), NOW, forecast(chance))


def actions_of(shortcut, identifier):
    return [
        action["WFWorkflowActionParameters"]
        for action in shortcut["WFWorkflowActions"]
        if action["WFWorkflowActionIdentifier"] == f"is.workflow.actions.{identifier}"
    ]


class WhatSiriSaysTests(unittest.TestCase):
    def test_full_rundown(self):
        self.assertEqual(
            say(0.8),
            "Right now it's 58°F and Cloudy. Today: Rain, with a high of 64°F and a low of 51°F. "
            "Chance of rain: 80%. Bring an umbrella.",
        )

    def test_rain_chance_reported_as_a_percent_reads_the_same(self):
        self.assertEqual(say(80), say(0.8))

    def test_no_umbrella_reminder_on_a_dry_day(self):
        self.assertTrue(say(0.1).endswith("Chance of rain: 10%."))
        self.assertTrue(say(10).endswith("Chance of rain: 10%."))

    def test_rain_chance_edges(self):
        self.assertIn("Chance of rain: 0%.", say(0))
        self.assertIn("Chance of rain: 100%. Bring an umbrella.", say(1.0))
        self.assertIn("Chance of rain: 100%. Bring an umbrella.", say(100))
        self.assertIn("Chance of rain: 29%.", say(0.29))  # 0.29 × 100 is 28.999…
        self.assertIn("Chance of rain: 50%. Bring an umbrella.", say(0.5))
        self.assertTrue(say(0.494).endswith("Chance of rain: 49%."))

    def test_umbrella_threshold_is_configurable(self):
        self.assertTrue(say(0.4).endswith("Chance of rain: 40%."))
        self.assertTrue(say(0.4, umbrella_at=40).endswith("Bring an umbrella."))

    def test_tomorrow_outlook(self):
        self.assertEqual(
            say(0.8, tomorrow=True),
            "Right now it's 58°F and Cloudy. Today: Rain, with a high of 64°F and a low of 51°F. "
            "Chance of rain: 80%. Bring an umbrella. "
            "Tomorrow: Partly Cloudy, with a high of 66°F and a low of 50°F.",
        )
        self.assertNotIn("Tomorrow", say(0.8))


class ShortcutFileTests(unittest.TestCase):
    def setUp(self):
        self.shortcut = build_shortcut.build()

    def test_committed_file_is_up_to_date(self):
        committed = plistlib.loads(build_shortcut.DEFAULT_OUTPUT.read_bytes())
        self.assertEqual(
            committed, self.shortcut,
            "shortcut/weather-rundown.unsigned.shortcut is stale; run python3 shortcut/build_shortcut.py",
        )

    def test_command_line_writes_a_loadable_plist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.shortcut"
            build_shortcut.main(["--tomorrow", "--umbrella-at", "40", "-o", str(path)])
            self.assertEqual(plistlib.loads(path.read_bytes()), build_shortcut.build(40, tomorrow=True))

    def test_shows_on_apple_watch(self):
        self.assertIn("Watch", self.shortcut["WFWorkflowTypes"])

    def test_every_action_uuid_is_unique(self):
        uuids = [
            action["WFWorkflowActionParameters"]["UUID"]
            for action in self.shortcut["WFWorkflowActions"]
            if "UUID" in action["WFWorkflowActionParameters"]
        ]
        self.assertEqual(len(uuids), len(set(uuids)))

    def test_ifs_are_properly_nested_with_both_branches(self):
        groups = control_flow_groups(self.shortcut["WFWorkflowActions"])
        self.assertEqual(len(groups), 2)
        for parts in groups.values():
            self.assertEqual(sorted(parts), [0, 1, 2])

    def test_uses_only_known_weather_details(self):
        for params in actions_of(self.shortcut, "properties.weather.conditions"):
            self.assertIn(params["WFContentItemPropertyName"], build_shortcut.WEATHER_DETAILS)

    def test_highs_and_lows_come_from_the_forecast(self):
        today = actions_of(self.shortcut, "getitemfromlist")[0]["UUID"]
        for params in actions_of(self.shortcut, "properties.weather.conditions"):
            if params["WFContentItemPropertyName"] in {"High", "Low"}:
                self.assertEqual(params["WFInput"]["Value"]["OutputUUID"], today)

    def test_ends_by_showing_the_rundown(self):
        last = self.shortcut["WFWorkflowActions"][-1]
        self.assertEqual(last["WFWorkflowActionIdentifier"], "is.workflow.actions.showresult")

    def test_text_placeholders_are_counted_in_utf16(self):
        emoji = build_shortcut.Output("A", "Emoji")
        field = build_shortcut.text("☔️🌂 ", emoji, "!")
        self.assertEqual(list(field["Value"]["attachmentsByRange"]), ["{5, 1}"])


class SimulatorTests(unittest.TestCase):
    """The simulator must reject the mistakes it exists to catch."""

    def test_rejects_use_before_definition(self):
        shortcut = build_shortcut.build()
        actions = shortcut["WFWorkflowActions"]
        actions.insert(0, actions.pop())  # show the rundown before it's written
        with self.assertRaisesRegex(SimulationError, "before its action runs"):
            run(shortcut, NOW, forecast(0.8))

    def test_rejects_an_empty_branch(self):
        builder = build_shortcut.ShortcutBuilder()
        days = builder.add("weather.forecast", output="Forecast")
        today = builder.add("getitemfromlist", {"WFInput": days.attachment()}, "Today")
        chance = builder.weather_detail(today, "Precipitation Chance", "Chance")
        group = builder.if_number(chance, build_shortcut.GREATER_THAN_OR_EQUAL, 1, "empty")
        builder.otherwise(group)
        builder.add("nothing")
        builder.end_if(group, "empty")
        with self.assertRaisesRegex(SimulationError, "ran no actions"):
            run({"WFWorkflowActions": builder.actions}, NOW, forecast(5))


if __name__ == "__main__":
    unittest.main()
