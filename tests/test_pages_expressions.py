"""Read the part of GitHub's expression language that the job conditions of pages.yml use, as GitHub reads it.

tests/test_pages.py and tests/test_pages_guards.py evaluate the conditions of pages.yml with evaluate() and truthy();
the tests below hold them to GitHub's documented rules.
"""

import json
import math
import re
import unittest

# The part of GitHub's expression language that the conditions of pages.yml use: string, true, false and null literals,
# context properties (null when absent), ! (true exactly when its operand is false, taken as && and || take it; it binds
# tighter than == and !=), == and !=, && and || (each gives one of its operands, and takes false, 0, NaN, '' and null
# for false, and anything else, an array or an object included, for true), parentheses, always(), cancelled(),
# startsWith() and toJSON(). == compares two strings ignoring case, two arrays or two objects as one instance or two,
# and two values of one other type as they are; values of two types become numbers first (null and false 0, true 1, a
# number itself, '' 0, another string its JSON number or NaN, an array or an object NaN; NaN equals nothing), and != is
# true exactly when == is false. always() is true, and cancelled() is true when the run was cancelled (the contexts say
# so under CANCELLED); in a job's condition both are status functions: GitHub then evaluates the condition whatever the
# jobs it needs did. startsWith() casts both arguments to strings (null to '') and ignores case. Any other syntax is an
# error.
EXPRESSION_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(==|!=|&&|\|\||[(),!])|([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*))")
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
LITERALS = {"true": True, "false": False, "null": None}


def as_number(value):
    if value is None or isinstance(value, bool):
        return float(bool(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return 0.0 if value == "" else float(value) if JSON_NUMBER.fullmatch(value) else math.nan
    return math.nan  # an array or an object


def as_text(value):
    return "" if value is None else str(value).lower() if isinstance(value, bool) else value


def truthy(value):
    if value is None or isinstance(value, bool):
        return value is True
    if isinstance(value, (int, float)):
        return value != 0 and not math.isnan(value)
    return value != ""  # a string, or an array or an object, which is never ''


def equal(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if type(left) is type(right):
        return left is right if isinstance(left, (list, dict)) else left == right
    return as_number(left) == as_number(right)


# Where the contexts say that the run was cancelled: no expression can name it, as a property path has no spaces.
CANCELLED = "the run was cancelled"
# Each function's number of arguments, and what it gives for the contexts and those arguments.
FUNCTIONS = {
    "always": (0, lambda contexts: True),
    "cancelled": (0, lambda contexts: contexts.get(CANCELLED) is True),
    "startsWith": (2, lambda contexts, text, prefix: as_text(text).casefold().startswith(as_text(prefix).casefold())),
    "toJSON": (1, lambda contexts, value: json.dumps(value, ensure_ascii=False)),
}


def tokenize(expression):
    """(string, operator, name) for each token of expression, with only one of the three set, and an end marker."""
    tokens, position = [], 0
    while expression[position:].strip():
        match = EXPRESSION_TOKEN.match(expression, position)
        if match is None:
            raise ValueError(f"unsupported expression syntax: {expression[position:]!r}")
        tokens.append(match.groups())
        position = match.end()
    return [*tokens, (None, None, None)]


class Evaluation:
    """An expression evaluated with contexts. Each method reads the tokens from index on and gives the value of what it
    read and the index after it: either() an || chain, both() an && chain, comparison() an == or a != or one operand,
    operand() a literal, a property, a call, a parenthesised expression or ! and its operand."""

    def __init__(self, expression, contexts):
        self.expression, self.contexts, self.tokens = expression, contexts, tokenize(expression)

    def expect(self, index, operator):
        if self.tokens[index][1] != operator:
            raise ValueError(f"expected {operator!r} in {self.expression!r}")
        return index + 1

    def operand(self, index):
        string, operator, name = self.tokens[index]
        if operator == "(":
            value, index = self.either(index + 1)
            return value, self.expect(index, ")")
        if operator == "!":
            value, index = self.operand(index + 1)
            return not truthy(value), index
        if string is not None:
            return string[1:-1].replace("''", "'"), index + 1
        if name is None:
            raise ValueError(f"expected an operand in {self.expression!r}")
        if name in LITERALS:
            return LITERALS[name], index + 1
        if self.tokens[index + 1][1] == "(":
            return self.call(name, index + 1)
        return self.contexts.get(name), index + 1

    def call(self, name, index):
        if name not in FUNCTIONS:
            raise ValueError(f"unsupported function {name}()")
        arity, function = FUNCTIONS[name]
        arguments = []
        if self.tokens[index + 1][1] == ")":
            index += 1  # no arguments: the ")" follows the "("
        else:
            while not arguments or self.tokens[index][1] == ",":
                value, index = self.either(index + 1)
                arguments.append(value)
        if len(arguments) != arity:
            raise ValueError(f"{name}() takes {arity} arguments")
        return function(self.contexts, *arguments), self.expect(index, ")")

    def comparison(self, index):
        value, index = self.operand(index)
        operator = self.tokens[index][1]
        if operator in ("==", "!="):
            right, index = self.operand(index + 1)
            value = equal(value, right) if operator == "==" else not equal(value, right)
        return value, index

    def both(self, index):
        value, index = self.comparison(index)
        while self.tokens[index][1] == "&&":
            right, index = self.comparison(index + 1)
            value = right if truthy(value) else value
        return value, index

    def either(self, index):
        value, index = self.both(index)
        while self.tokens[index][1] == "||":
            right, index = self.both(index + 1)
            value = value if truthy(value) else right
        return value, index


def evaluate(expression, contexts):
    """The value of expression, with contexts mapping property paths such as github.ref to their values."""
    evaluation = Evaluation(expression, contexts)
    value, index = evaluation.either(0)
    if index != len(evaluation.tokens) - 1:
        raise ValueError(f"unexpected {evaluation.tokens[index]} in {expression!r}")
    return value


class ExpressionTests(unittest.TestCase):
    """evaluate() follows GitHub's documented rules for the expressions it reads and refuses every other one, so the
    deploy condition cannot pass these tests by being misread."""

    def test_values_compare_and_combine_as_on_github(self):
        for expression, contexts, value in (
            ("'Refs/Tags/V1' == 'refs/tags/v1'", {}, True),
            ("'1' == '01'", {}, False),
            ("a.b == '1'", {}, False),
            ("a.b == ''", {}, True),
            ("a.b == '2'", {"a.b": ""}, False),
            ("a.b == 'x'", {}, False),
            ("true == '1'", {}, True),
            ("false == null", {}, True),
            ("toJSON(false) == 'false'", {}, True),
            ("toJSON(a.b)", {}, "null"),
            ("toJSON(a.b)", {"a.b": "false"}, '"false"'),
            ("startsWith(a.b, 'REFS/')", {"a.b": "refs/tags/v1"}, True),
            ("startsWith(a.b, 'refs/')", {}, False),
            ("a.b && 'yes' || 'no'", {"a.b": ""}, "no"),
            ("a.b && 'yes' || 'no'", {"a.b": "x"}, "yes"),
            ("a.b && 'yes' || 'no'", {}, "no"),
            # A context may hold a number: it stays that number, sign included, and only 0 and NaN are false.
            ("a.b == c.d", {"a.b": "2", "c.d": 2}, True),
            ("a.b == c.d", {"a.b": "1", "c.d": 2}, False),
            ("a.b == '0.5'", {"a.b": 0.5}, True),
            ("a.b == '-1'", {"a.b": -1}, True),
            ("a.b == true", {"a.b": 2}, False),
            ("a.b == null", {"a.b": 0}, True),
            ("a.b && 'yes' || 'no'", {"a.b": 2}, "yes"),
            ("a.b && 'yes' || 'no'", {"a.b": -1}, "yes"),
            ("a.b && 'yes' || 'no'", {"a.b": 0}, "no"),
            ("a.b && 'yes' || 'no'", {"a.b": math.nan}, "no"),
            # Or an array or an object: it is true, and NaN next to a value of another type.
            ("a.b && 'yes' || 'no'", {"a.b": []}, "yes"),
            ("a.b && 'yes' || 'no'", {"a.b": {}}, "yes"),
            ("a.b == null", {"a.b": []}, False),
            ("a.b == ''", {"a.b": {}}, False),
            ("a.b == 'x'", {"a.b": []}, False),
            ("a.b == c.d", {"a.b": {}, "c.d": "y"}, False),
            # Two arrays or two objects are equal only as one instance, whatever they hold.
            ("a.b == a.b", {"a.b": []}, True),
            ("a.b == a.b", {"a.b": {"x": 1}}, True),
            ("a.b == c.d", {"a.b": [], "c.d": []}, False),
            ("a.b == c.d", {"a.b": {"x": 1}, "c.d": {"x": 1}}, False),
            ("a.b != c.d", {"a.b": ["x"], "c.d": ["x"]}, True),
            ("('it''s' || a.b) == 'IT''S'", {}, True),
            # != is true exactly when == is false, with the same casts: case, null and '', strings and numbers, NaN.
            ("'Refs/Tags/V1' != 'refs/tags/v1'", {}, False),
            ("a.b != '1'", {"a.b": "2"}, True),
            ("a.b != '10'", {"a.b": "1"}, True),
            ("a.b != ''", {}, False),
            ("a.b != c.d", {"a.b": "2", "c.d": 2}, False),
            ("a.b != c.d", {"a.b": "1", "c.d": 2}, True),
            ("a.b != a.b", {"a.b": math.nan}, True),
            ("a.b != null", {"a.b": []}, True),
            ("true != '1'", {}, False),
            ("a.b != 'x' && 'yes' || 'no'", {"a.b": "X"}, "no"),
            # always() takes no argument and is true.
            ("always()", {}, True),
            ("always() && a.b", {"a.b": "x"}, "x"),
            ("always() && a.b == 'x' && 'yes' || 'no'", {}, "no"),
            # cancelled() takes no argument and is true only when the contexts say the run was cancelled.
            ("cancelled()", {}, False),
            ("cancelled()", {CANCELLED: True}, True),
            ("cancelled()", {CANCELLED: "true"}, False),
            # ! gives true or false: true exactly for what && and || take for false. It binds tighter than ==, !=, &&
            # and ||, and applies to a parenthesised expression or a call as a whole.
            ("!a.b", {}, True),
            ("!a.b", {"a.b": ""}, True),
            ("!a.b", {"a.b": "x"}, False),
            ("!a.b", {"a.b": 0}, True),
            ("!a.b", {"a.b": []}, False),
            ("toJSON(!a.b)", {"a.b": "x"}, "false"),
            ("!!a.b", {"a.b": "x"}, True),
            ("! a.b", {"a.b": "x"}, False),
            ("!a.b == true", {"a.b": "0"}, False),
            ("!a.b != false", {"a.b": "0"}, False),
            ("!a.b && 'yes' || 'no'", {"a.b": ""}, "yes"),
            ("!(a.b == 'x')", {"a.b": "X"}, False),
            ("!always()", {}, False),
            ("!cancelled()", {}, True),
            ("!cancelled()", {CANCELLED: True}, False),
            ("!cancelled() && a.b == 'x'", {"a.b": "x"}, True),
        ):
            with self.subTest(expression, contexts=contexts):
                self.assertEqual(evaluate(expression, contexts), value)

    def test_other_syntax_is_refused(self):
        for expression in (
            "a !== b",
            "a <> b",
            "a != b != c",
            "a == b != c",
            "!",
            "a !",
            "a ! b",
            "!= a",
            "!(a",
            "always(a)",
            "cancelled(a)",
            "success()",
            "a[0]",
            "1 == 1",
            "a == b == c",
            "fromJSON(a)",
            "startsWith(a)",
            "(a",
            "a)",
            "",
        ):
            with self.subTest(expression), self.assertRaises(ValueError):
                evaluate(expression, {})


if __name__ == "__main__":
    unittest.main()
