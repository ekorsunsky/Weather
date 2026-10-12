"""A small interpreter for the Shortcuts actions the generator uses.

It runs a generated shortcut against made-up weather and returns what
"Show Content" would display (and Siri would say), so the wiring between
actions can be tested without an Apple device.
"""

from __future__ import annotations

import math
import operator
import re

OBJECT_REPLACEMENT = "￼"

COMPARISONS = {
    0: operator.lt,
    1: operator.le,
    2: operator.gt,
    3: operator.ge,
    4: operator.eq,
    5: operator.ne,
}
MATH = {"+": operator.add, "-": operator.sub, "×": operator.mul, "÷": operator.truediv}


class SimulationError(Exception):
    pass


def control_flow_groups(actions: list[dict]) -> dict[str, dict[int, int]]:
    """Map each If's GroupingIdentifier to {mode: action index}, checking nesting."""
    groups: dict[str, dict[int, int]] = {}
    open_groups: list[str] = []
    for index, action in enumerate(actions):
        if action["WFWorkflowActionIdentifier"] != "is.workflow.actions.conditional":
            continue
        params = action["WFWorkflowActionParameters"]
        group, mode = params["GroupingIdentifier"], params["WFControlFlowMode"]
        if mode == 0:
            if group in groups:
                raise SimulationError(f"If {group} starts twice")
            groups[group] = {0: index}
            open_groups.append(group)
            continue
        if not open_groups or open_groups[-1] != group:
            raise SimulationError(f"action {index} closes If {group} out of order")
        if mode in groups[group]:
            raise SimulationError(f"If {group} has two parts with mode {mode}")
        groups[group][mode] = index
        if mode == 2:
            open_groups.pop()
    if open_groups:
        raise SimulationError(f"If {open_groups[-1]} never ends")
    return groups


def run(shortcut: dict, now: dict, forecast: list[dict]) -> str:
    actions = shortcut["WFWorkflowActions"]
    groups = control_flow_groups(actions)
    outputs: dict[str, object] = {}
    shown: list[str] = []
    branch_starts: list[int] = []
    executed = 0
    last: object = None
    index = 0

    while index < len(actions):
        identifier = actions[index]["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.")
        params = actions[index]["WFWorkflowActionParameters"]

        if identifier == "conditional":
            parts = groups[params["GroupingIdentifier"]]
            mode = params["WFControlFlowMode"]
            if mode == 0:
                value = number(resolve(params["WFInput"]["Variable"], outputs))
                matched = COMPARISONS[params["WFCondition"]](value, float(params["WFNumberValue"]))
                branch_starts.append(executed)
                index = index + 1 if matched else parts.get(1, parts[2] - 1) + 1
            elif mode == 1:  # the "then" branch finished; skip "Otherwise"
                index = parts[2]
            else:
                if executed == branch_starts.pop():
                    # What If Result holds after an empty branch isn't something
                    # to rely on, so the generator must never produce one.
                    raise SimulationError("an If branch ran no actions")
                if "UUID" in params:
                    outputs[params["UUID"]] = last
                index += 1
            continue

        if identifier == "weather.forecast":
            if params.get("WFWeatherForecastType", "Daily") != "Daily":
                raise SimulationError("expected a daily forecast")
            last = list(forecast)
        elif identifier == "weather.currentconditions":
            last = dict(now)
        elif identifier == "getitemfromlist":
            items = resolve(params["WFInput"], outputs)
            specifier = params.get("WFItemSpecifier", "First Item")
            if specifier == "First Item":
                last = items[0]
            elif specifier == "Item At Index":
                last = items[int(params["WFItemIndex"]) - 1]  # Shortcuts counts from 1
            else:
                raise SimulationError(f"unsupported item specifier {specifier!r}")
        elif identifier == "properties.weather.conditions":
            last = resolve(params["WFInput"], outputs)[params["WFContentItemPropertyName"]]
        elif identifier == "math":
            operation = MATH[params.get("WFMathOperation", "+")]
            last = operation(number(resolve(params["WFInput"], outputs)), number(params["WFMathOperand"]))
        elif identifier == "round":
            if params.get("WFRoundMode", "Normal") != "Normal":
                raise SimulationError("expected normal rounding")
            last = math.floor(number(resolve(params["WFInput"], outputs)) + 0.5)
        elif identifier == "gettext":
            last = render(params.get("WFTextActionText"), outputs)
        elif identifier == "nothing":
            last = None
        elif identifier == "showresult":
            shown.append(render(params["Text"], outputs))
        else:
            raise SimulationError(f"the simulator doesn't know {identifier}")

        if "UUID" in params:
            outputs[params["UUID"]] = last
        executed += 1
        index += 1

    return "\n".join(shown)


def resolve(parameter: dict, outputs: dict) -> object:
    """Look up the action output an input parameter points to."""
    if parameter.get("WFSerializationType") != "WFTextTokenAttachment":
        raise SimulationError(f"expected a variable, got {parameter!r}")
    return lookup(parameter["Value"], outputs)


def lookup(token: dict, outputs: dict) -> object:
    if token.get("Type") != "ActionOutput":
        raise SimulationError(f"unsupported variable {token!r}")
    if token["OutputUUID"] not in outputs:
        raise SimulationError(f"{token['OutputName']!r} is used before its action runs")
    return outputs[token["OutputUUID"]]


def render(field: dict | None, outputs: dict) -> str:
    """Fill a text field's placeholders with the values they point to."""
    if field is None:
        return ""
    if field.get("WFSerializationType") != "WFTextTokenString":
        raise SimulationError(f"expected a text field, got {field!r}")
    string = field["Value"]["string"]
    tokens = {}
    for key, token in field["Value"].get("attachmentsByRange", {}).items():
        position, length = map(int, re.fullmatch(r"\{(\d+), (\d+)\}", key).groups())
        if length != 1:
            raise SimulationError(f"attachment {key} should cover one character")
        tokens[position] = token

    rendered = []
    position = 0  # in UTF-16 code units, like the attachment ranges
    for character in string:
        if character == OBJECT_REPLACEMENT:
            if position not in tokens:
                raise SimulationError(f"placeholder at {position} has no attachment")
            rendered.append(display(lookup(tokens.pop(position), outputs)))
        else:
            rendered.append(character)
        position += len(character.encode("utf-16-le")) // 2
    if tokens:
        raise SimulationError(f"attachments at {sorted(tokens)} don't point at a placeholder")
    return "".join(rendered)


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationError(f"expected a number, got {value!r}")
    return value


def display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
