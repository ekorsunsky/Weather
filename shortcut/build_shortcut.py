#!/usr/bin/env python3
"""Generate the "Weather Rundown" Siri Shortcut.

Siri's built-in weather answer only covers current conditions. This shortcut
also gives today's conditions, high and low, chance of rain, and an umbrella
reminder:

    Right now it's 58°F and Cloudy. Today: Rain, with a high of 64°F and a low
    of 51°F. Chance of rain: 80%. Bring an umbrella.

The output is an unsigned .shortcut file (an XML property list). Apple only
imports signed shortcuts, so on a Mac run sign-on-mac.sh next.

    python3 shortcut/build_shortcut.py                  # today's rundown
    python3 shortcut/build_shortcut.py --tomorrow       # ...plus tomorrow
    python3 shortcut/build_shortcut.py --umbrella-at 40
"""

from __future__ import annotations

import argparse
import plistlib
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "weather-rundown.unsigned.shortcut"

# Shortcuts marks each inline variable in a text field with this character and
# records what belongs there under "attachmentsByRange".
OBJECT_REPLACEMENT = "￼"

# Fixed so every build produces the same UUIDs and the file diffs cleanly.
UUID_NAMESPACE = uuid.UUID("5f0c1b8e-4a57-4d0b-9f3e-2b9c7c1d6a10")

# Details that "Get Details of Weather Conditions" can return.
WEATHER_DETAILS = frozenset({
    "Air Pollutants", "Air Quality Category", "Air Quality Index", "Condition",
    "Date", "Dewpoint", "Feels Like", "High", "Humidity", "Location", "Low",
    "Name", "Precipitation Amount", "Precipitation Chance", "Pressure",
    "Sunrise Time", "Sunset Time", "Temperature", "UV Index", "Visibility",
    "Wind Direction", "Wind Speed",
})

# Comparison codes used by the "If" action.
LESS_THAN_OR_EQUAL = 1
GREATER_THAN_OR_EQUAL = 3

CLIENT_VERSION = "4033.0.4.3"  # Shortcuts on iOS 26
SUN_GLYPH = 59845
LIGHT_BLUE = 1440408063

# What Shortcuts lists by default for a shortcut that takes no input.
DEFAULT_INPUT_CLASSES = [
    "WFAppContentItem", "WFAppStoreAppContentItem", "WFArticleContentItem",
    "WFContactContentItem", "WFDateContentItem", "WFEmailAddressContentItem",
    "WFFolderContentItem", "WFGenericFileContentItem", "WFImageContentItem",
    "WFiTunesProductContentItem", "WFLocationContentItem",
    "WFDCMapsLinkContentItem", "WFAVAssetContentItem", "WFPDFContentItem",
    "WFPhoneNumberContentItem", "WFRichTextContentItem",
    "WFSafariWebPageContentItem", "WFStringContentItem", "WFURLContentItem",
]


class Output:
    """The output of an earlier action (a "magic variable" in Shortcuts)."""

    def __init__(self, action_uuid: str, name: str):
        self.uuid = action_uuid
        self.name = name

    def token(self) -> dict:
        return {"Type": "ActionOutput", "OutputUUID": self.uuid, "OutputName": self.name}

    def attachment(self) -> dict:
        """The form used when the output is passed as an action's input."""
        return {"Value": self.token(), "WFSerializationType": "WFTextTokenAttachment"}


def text(*parts: str | Output) -> dict:
    """A text field made of plain strings with variables mixed in."""
    string = ""
    attachments = {}
    for part in parts:
        if isinstance(part, Output):
            # Positions are counted in UTF-16 code units, as NSString does.
            position = len(string.encode("utf-16-le")) // 2
            attachments[f"{{{position}, 1}}"] = part.token()
            string += OBJECT_REPLACEMENT
        else:
            string += part
    value = {"string": string}
    if attachments:
        value["attachmentsByRange"] = attachments
    return {"Value": value, "WFSerializationType": "WFTextTokenString"}


class ShortcutBuilder:
    def __init__(self):
        self.actions: list[dict] = []
        self._labels: set[str] = set()

    def _uuid(self, label: str) -> str:
        if label in self._labels:
            raise ValueError(f"duplicate label {label!r}")
        self._labels.add(label)
        return str(uuid.uuid5(UUID_NAMESPACE, label)).upper()

    def add(self, identifier: str, params: dict | None = None, output: str | None = None) -> Output | None:
        """Append an action. Naming its output makes it referable later."""
        params = dict(params or {})
        result = None
        if output:
            result = Output(self._uuid(output), output)
            params["UUID"] = result.uuid
            params["CustomOutputName"] = output
        self.actions.append({
            "WFWorkflowActionIdentifier": f"is.workflow.actions.{identifier}",
            "WFWorkflowActionParameters": params,
        })
        return result

    def weather_detail(self, weather: Output, detail: str, output: str) -> Output:
        if detail not in WEATHER_DETAILS:
            raise ValueError(f"unknown weather detail {detail!r}")
        return self.add("properties.weather.conditions", {
            "WFInput": weather.attachment(),
            "WFContentItemPropertyName": detail,
        }, output)

    def text_action(self, *parts: str | Output, output: str | None = None) -> Output | None:
        return self.add("gettext", {"WFTextActionText": text(*parts)}, output)

    def multiply(self, number: Output, by: float) -> None:
        self.add("math", {"WFInput": number.attachment(), "WFMathOperation": "×", "WFMathOperand": by})

    def if_number(self, number: Output, condition: int, value: float, label: str) -> str:
        group = self._uuid(f"if {label}")
        self.add("conditional", {
            "GroupingIdentifier": group,
            "WFControlFlowMode": 0,
            "WFCondition": condition,
            "WFInput": {"Type": "Variable", "Variable": number.attachment()},
            "WFNumberValue": f"{value:g}",
        })
        return group

    def otherwise(self, group: str) -> None:
        self.add("conditional", {"GroupingIdentifier": group, "WFControlFlowMode": 1})

    def end_if(self, group: str, label: str) -> Output:
        """Close an If; its result is the output of whichever branch ran."""
        result = Output(self._uuid(f"end if {label}"), "If Result")
        self.add("conditional", {
            "GroupingIdentifier": group,
            "WFControlFlowMode": 2,
            "UUID": result.uuid,
        })
        return result


def build(umbrella_at: float = 50, tomorrow: bool = False) -> dict:
    """Return the shortcut as a property-list dictionary."""
    b = ShortcutBuilder()

    # Highs and lows come from the daily forecast: Get Current Weather's
    # "High" covers the past 24 hours, so in the morning it can be yesterday's.
    forecast = b.add("weather.forecast", {"WFWeatherForecastType": "Daily"}, "Forecast")
    today = b.add("getitemfromlist", {
        "WFInput": forecast.attachment(),
        "WFItemSpecifier": "First Item",
    }, "Today")
    high = b.weather_detail(today, "High", "High")
    low = b.weather_detail(today, "Low", "Low")
    sky_today = b.weather_detail(today, "Condition", "Condition Today")
    chance = b.weather_detail(today, "Precipitation Chance", "Precipitation Chance")

    # Depending on the device, Shortcuts reports a 40% chance as 0.4 or as 40.
    # Turn either into a whole percentage.
    group = b.if_number(chance, LESS_THAN_OR_EQUAL, 1, "rain is a fraction")
    b.multiply(chance, 100)
    b.otherwise(group)
    b.multiply(chance, 1)
    percent = b.end_if(group, "rain is a fraction")
    rain = b.add("round", {"WFInput": percent.attachment(), "WFRoundMode": "Normal"}, "Rain Chance")

    group = b.if_number(rain, GREATER_THAN_OR_EQUAL, umbrella_at, "umbrella")
    b.text_action(" Bring an umbrella.")
    b.otherwise(group)
    b.add("nothing")
    umbrella = b.end_if(group, "umbrella")

    now = b.add("weather.currentconditions", output="Now")
    temperature = b.weather_detail(now, "Temperature", "Temperature Now")
    sky_now = b.weather_detail(now, "Condition", "Condition Now")

    message = [
        "Right now it's ", temperature, " and ", sky_now, ". ",
        "Today: ", sky_today, ", with a high of ", high, " and a low of ", low, ". ",
        "Chance of rain: ", rain, "%.", umbrella,
    ]

    if tomorrow:
        next_day = b.add("getitemfromlist", {
            "WFInput": forecast.attachment(),
            "WFItemSpecifier": "Item At Index",
            "WFItemIndex": 2,
        }, "Tomorrow")
        sky_next = b.weather_detail(next_day, "Condition", "Condition Tomorrow")
        high_next = b.weather_detail(next_day, "High", "High Tomorrow")
        low_next = b.weather_detail(next_day, "Low", "Low Tomorrow")
        message += [
            " Tomorrow: ", sky_next, ", with a high of ", high_next,
            " and a low of ", low_next, ".",
        ]

    rundown = b.text_action(*message, output="Weather Rundown")
    # Siri reads this aloud. (Called "Show Result" before iOS 26.)
    b.add("showresult", {"Text": text(rundown)})

    return {
        "WFWorkflowActions": b.actions,
        "WFWorkflowClientVersion": CLIENT_VERSION,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": SUN_GLYPH,
            "WFWorkflowIconStartColor": LIGHT_BLUE,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": DEFAULT_INPUT_CLASSES,
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowOutputContentItemClasses": [],
        "WFQuickActionSurfaces": [],
        "WFWorkflowTypes": ["Watch"],  # "Show on Apple Watch"
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Weather Rundown Siri Shortcut (unsigned).")
    parser.add_argument("--tomorrow", action="store_true", help="also give tomorrow's outlook")
    parser.add_argument("--umbrella-at", type=float, default=50, metavar="PERCENT",
                        help="chance of rain that triggers the umbrella reminder (default: 50)")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"where to write the file (default: {DEFAULT_OUTPUT.name})")
    args = parser.parse_args(argv)
    if not 0 <= args.umbrella_at <= 100:
        parser.error("--umbrella-at must be between 0 and 100")

    shortcut = build(umbrella_at=args.umbrella_at, tomorrow=args.tomorrow)
    args.output.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_XML))
    print(f"Wrote {args.output}")
    custom = "" if args.output.resolve() == DEFAULT_OUTPUT else f' "{args.output}"'
    print(f"Next, on a Mac: sh shortcut/sign-on-mac.sh{custom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
