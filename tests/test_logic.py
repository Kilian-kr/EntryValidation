"""Full logic check — run with: python tests/test_logic.py"""
import sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from core.validation import validate_cell, run_validation
from core.schema_match import build_manual_schema, match_template_to_columns, build_resolved_schema
from core.stats import build_invalid_index, get_invalid_row_set
from core.export import issues_to_csv, wrong_rows_to_csv

PASS = 0; FAIL = 0

def check(name, got, expected):
    global PASS, FAIL
    ok = (got == expected)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        print(f"        expected = {expected!r}")
        print(f"        got      = {got!r}")

def codes(result):
    return [c for c, _ in result]

# ── 1. NULL HANDLING ──────────────────────────────────────────────────────────
col_req = {"type": "string", "required": True,  "nullable": False, "rules": {}}
col_opt = {"type": "string", "required": False, "nullable": True,  "rules": {}}

check("null empty req",     codes(validate_cell("",      col_req, [])), ["REQUIRED_EMPTY"])
check("null spaces req",    codes(validate_cell("   ",   col_req, [])), ["REQUIRED_EMPTY"])
check("null NA req",        codes(validate_cell("NA",    col_req, [])), ["REQUIRED_EMPTY"])
check("null N/A req",       codes(validate_cell("N/A",   col_req, [])), ["REQUIRED_EMPTY"])
check("null null req",      codes(validate_cell("null",  col_req, [])), ["REQUIRED_EMPTY"])
check("null NULL req",      codes(validate_cell("NULL",  col_req, [])), ["REQUIRED_EMPTY"])
check("null - req",         codes(validate_cell("-",     col_req, [])), ["REQUIRED_EMPTY"])
check("null -- req",        codes(validate_cell("--",    col_req, [])), ["REQUIRED_EMPTY"])
check("null na lower req",  codes(validate_cell("na",    col_req, [])), ["REQUIRED_EMPTY"])
check("null empty opt",     codes(validate_cell("",      col_opt, [])), [])
check("null spaces opt",    codes(validate_cell("  ",    col_opt, [])), [])
check("custom null token",  codes(validate_cell("MISSING", col_req, ["MISSING"])), ["REQUIRED_EMPTY"])
check("valid string",       codes(validate_cell("hello", col_opt, [])), [])

# ── 2. STRING / TEXT RULES ────────────────────────────────────────────────────
col_str = {"type": "string", "required": False, "nullable": True,
           "rules": {"min_len": 3, "max_len": 6, "regex": r"^[A-Z]+"}}
check("string ok",           codes(validate_cell("ABC",     col_str, [])), [])
check("string min_len fail", codes(validate_cell("AB",      col_str, [])), ["MIN_LEN_VIOLATED"])
check("string max_len fail", codes(validate_cell("ABCDEFG", col_str, [])), ["MAX_LEN_VIOLATED"])
check("string regex fail",   codes(validate_cell("abc",     col_str, [])), ["REGEX_MISMATCH"])
col_txt = {"type": "text", "required": False, "nullable": True, "rules": {"max_len": 4}}
check("text alias ok",       codes(validate_cell("hi",      col_txt, [])), [])
check("text alias fail",     codes(validate_cell("toolong", col_txt, [])), ["MAX_LEN_VIOLATED"])

# ── 3. INTEGER ────────────────────────────────────────────────────────────────
col_int = {"type": "integer", "required": False, "nullable": True, "rules": {}}
col_int_rng = {"type": "integer", "required": False, "nullable": True, "rules": {"min": 0, "max": 100}}
check("int ok",          codes(validate_cell("42",   col_int,     [])), [])
check("int negative",    codes(validate_cell("-5",   col_int,     [])), [])
check("int zero",        codes(validate_cell("0",    col_int,     [])), [])
check("int decimal fail",codes(validate_cell("3.14", col_int,     [])), ["TYPE_MISMATCH"])
check("int text fail",   codes(validate_cell("abc",  col_int,     [])), ["TYPE_MISMATCH"])
check("int min ok",      codes(validate_cell("0",    col_int_rng, [])), [])
check("int max ok",      codes(validate_cell("100",  col_int_rng, [])), [])
check("int below min",   codes(validate_cell("-1",   col_int_rng, [])), ["MIN_VIOLATED"])
check("int above max",   codes(validate_cell("101",  col_int_rng, [])), ["MAX_VIOLATED"])

# ── 4. NUMBER ─────────────────────────────────────────────────────────────────
col_num = {"type": "number", "required": False, "nullable": True, "rules": {}}
col_num_rng = {"type": "number", "required": False, "nullable": True, "rules": {"min": 0.0, "max": 1.0}}
check("num float ok",   codes(validate_cell("3.14",  col_num,     [])), [])
check("num int ok",     codes(validate_cell("42",    col_num,     [])), [])
check("num neg ok",     codes(validate_cell("-0.5",  col_num,     [])), [])
check("num sci ok",     codes(validate_cell("1e6",   col_num,     [])), [])
check("num text fail",  codes(validate_cell("abc",   col_num,     [])), ["TYPE_MISMATCH"])
check("num min fail",   codes(validate_cell("-0.1",  col_num_rng, [])), ["MIN_VIOLATED"])
check("num max fail",   codes(validate_cell("1.001", col_num_rng, [])), ["MAX_VIOLATED"])
check("num inf fail",   codes(validate_cell("inf",   col_num,     [])), ["TYPE_MISMATCH"])
check("num nan fail",   codes(validate_cell("nan",   col_num,     [])), ["TYPE_MISMATCH"])
check("num -inf fail",  codes(validate_cell("-inf",  col_num,     [])), ["TYPE_MISMATCH"])
check("num Inf fail",   codes(validate_cell("Inf",   col_num,     [])), ["TYPE_MISMATCH"])

# ── 5. BOOLEAN ────────────────────────────────────────────────────────────────
col_bool = {"type": "boolean", "required": False, "nullable": True, "rules": {}}
for v in ["true", "True", "TRUE", "false", "False", "yes", "Yes", "NO", "1", "0"]:
    check(f"bool {v}", codes(validate_cell(v, col_bool, [])), [])
check("bool Y fail",  codes(validate_cell("Y", col_bool, [])), ["TYPE_MISMATCH"])
check("bool T fail",  codes(validate_cell("T", col_bool, [])), ["TYPE_MISMATCH"])
check("bool 2 fail",  codes(validate_cell("2", col_bool, [])), ["TYPE_MISMATCH"])

# ── 6. DATE ───────────────────────────────────────────────────────────────────
col_date = {"type": "date", "required": False, "nullable": True,
            "rules": {"formats": ["%Y-%m-%d", "%d.%m.%Y"]}}
col_date_nofmt = {"type": "date", "required": False, "nullable": True, "rules": {}}
check("date iso ok",   codes(validate_cell("2024-01-31", col_date,      [])), [])
check("date dot ok",   codes(validate_cell("31.01.2024", col_date,      [])), [])
check("date bad fmt",  codes(validate_cell("01/31/2024", col_date,      [])), ["DATE_PARSE_FAILED"])
check("date no fmt",   codes(validate_cell("2024-01-31", col_date_nofmt,[])), ["DATE_PARSE_FAILED"])
check("date garbage",  codes(validate_cell("notadate",   col_date,      [])), ["DATE_PARSE_FAILED"])

# ── 7. DATETIME ───────────────────────────────────────────────────────────────
col_dt = {"type": "datetime", "required": False, "nullable": True,
          "rules": {"formats": ["%Y-%m-%d %H:%M:%S"]}}
check("datetime ok",   codes(validate_cell("2024-01-31 14:30:00", col_dt, [])), [])
check("datetime fail", codes(validate_cell("2024-01-31",           col_dt, [])), ["DATE_PARSE_FAILED"])

# ── 8. ENUM ───────────────────────────────────────────────────────────────────
col_enum = {"type": "enum", "required": False, "nullable": True,
            "rules": {"allowed": ["RED", "GREEN", "BLUE"]}}
check("enum ok",       codes(validate_cell("RED",    col_enum, [])), [])
check("enum lower ok", codes(validate_cell("green",  col_enum, [])), [])
check("enum mixed ok", codes(validate_cell("Blue",   col_enum, [])), [])
check("enum fail",     codes(validate_cell("PURPLE", col_enum, [])), ["ENUM_NOT_ALLOWED"])
check("enum empty ok", codes(validate_cell("",       col_enum, [])), [])

# ── 9. UNIQUE CHECK ───────────────────────────────────────────────────────────
df_uniq = pd.DataFrame({"ID": ["A1", "A2", "A1", "A3", ""]})
schema_uniq = build_manual_schema("t", ["ID"], {"ID": "string"})
schema_uniq["columns"]["ID"]["rules"]    = {"unique": True}
schema_uniq["columns"]["ID"]["nullable"] = True
res_uniq = run_validation(df_uniq, schema_uniq)
uniq_rows = {(ci["row"], ci["code"]) for ci in res_uniq["cell_issues"]}
check("unique dup row2 flagged",  (2, "UNIQUE_VIOLATED") in uniq_rows, True)
check("unique first not flagged", (0, "UNIQUE_VIOLATED") in uniq_rows, False)
check("unique null not flagged",  any(ci["row"] == 4 for ci in res_uniq["cell_issues"]), False)

# ── 10. RUN_VALIDATION: STATS & COUNTERS ─────────────────────────────────────
df_v = pd.DataFrame({
    "Name":  ["Alice", "",     "Bob",  "Alice"],
    "Age":   ["30",    "25",   "abc",  "200"],
    "Score": ["0.9",   "0.5",  "1.5",  "0.8"],
})
schema_v = build_manual_schema("v", ["Name", "Age", "Score"],
    {"Name": "string", "Age": "integer", "Score": "number"},
    {"Name": "error",  "Age": "allowed", "Score": "allowed"})
schema_v["columns"]["Age"]["rules"]   = {"min": 0, "max": 150}
schema_v["columns"]["Score"]["rules"] = {"min": 0.0, "max": 1.0}
rv = run_validation(df_v, schema_v)

ci_index = {(c["row"], c["column"], c["code"]) for c in rv["cell_issues"]}
check("stats rows",             rv["stats"]["rows"],         4)
check("stats columns",          rv["stats"]["columns"],      3)
check("stats total_cells",      rv["stats"]["total_cells"],  12)
check("Name row1 REQUIRED_EMPTY", ("REQUIRED_EMPTY" in
    {c["code"] for c in rv["cell_issues"] if c["row"]==1 and c["column"]=="Name"}), True)
check("Age row2 TYPE_MISMATCH",   ("TYPE_MISMATCH"  in
    {c["code"] for c in rv["cell_issues"] if c["row"]==2 and c["column"]=="Age"}),  True)
check("Age row3 MAX_VIOLATED",    ("MAX_VIOLATED"   in
    {c["code"] for c in rv["cell_issues"] if c["row"]==3 and c["column"]=="Age"}),  True)
check("Score row2 MAX_VIOLATED",  ("MAX_VIOLATED"   in
    {c["code"] for c in rv["cell_issues"] if c["row"]==2 and c["column"]=="Score"}),True)
deduped = len({(c["row"], c["column"]) for c in rv["cell_issues"]})
check("invalid_cells == deduped", rv["stats"]["invalid_cells"], deduped)
check("worst_col not None",       rv["stats"]["worst_column"] is not None, True)

# ── 11. MISSING COLUMN + STRICT MODE ─────────────────────────────────────────
tmpl_strict = {
    "id": "s", "name": "S", "strict": True, "null_values": [],
    "columns": [
        {"name": "X", "type": "string", "required": True,  "nullable": True, "rules": {}},
        {"name": "Y", "type": "string", "required": False, "nullable": True, "rules": {}},
    ]
}
df_strict = pd.DataFrame({"Y": ["a"], "Z": ["b"]})
# X has no file mapping → missing; Z is extra
schema_strict = build_resolved_schema(tmpl_strict, {"Y": "Y"}, ["Y", "Z"])
schema_strict["dataset_id"] = "x"
rv_strict = run_validation(df_strict, schema_strict)
di_codes = {d["code"] for d in rv_strict["dataset_issues"]}
check("strict EXTRA_COLUMN",    "EXTRA_COLUMN"  in di_codes, True)

# ── 12. SCHEMA MATCH: ALIASES + EXTRAS ───────────────────────────────────────
tmpl_m = {
    "id": "m", "name": "M", "columns": [
        {"name": "ClientID", "aliases": ["client_id", "CLIENTID"],
         "type": "string", "required": True,  "nullable": False, "rules": {}},
        {"name": "Amount",   "aliases": ["amount_eur"],
         "type": "number",  "required": True,  "nullable": False, "rules": {}},
    ]
}
res_m = match_template_to_columns(tmpl_m, ["CLIENTID", "amount_eur", "Extra"])
check("alias ClientID matched",  res_m["matched"].get("ClientID"), "CLIENTID")
check("alias Amount matched",    res_m["matched"].get("Amount"),   "amount_eur")
check("extra col present",       "Extra" in res_m["extra"],        True)
check("no missing cols",         res_m["missing"],                 [])

res_none = match_template_to_columns(tmpl_m, ["foo", "bar"])
check("both unresolved",  len(res_none["unresolved"]), 2)
check("both missing",     len(res_none["missing"]),    2)

# ── 13. BUILD_MANUAL_SCHEMA: empty_map wiring ────────────────────────────────
s = build_manual_schema("x", ["A", "B"],
    {"A": "integer", "B": "string"},
    {"A": "error",   "B": "allowed"})
check("empty=error  => required",   s["columns"]["A"]["required"],  True)
check("empty=error  => nullable",   s["columns"]["A"]["nullable"],  False)
check("empty=allowed=> required",   s["columns"]["B"]["required"],  False)
check("empty=allowed=> nullable",   s["columns"]["B"]["nullable"],  True)

# ── 14. EXPORT ────────────────────────────────────────────────────────────────
fake_issues = {
    "cell_issues": [
        {"row": 0, "column": "A", "code": "TYPE_MISMATCH",   "message": "bad"},
        {"row": 2, "column": "B", "code": "REQUIRED_EMPTY",  "message": "empty"},
    ]
}
df_exp = pd.DataFrame({"A": ["x", "y", "z"], "B": ["1", "2", "3"]})
csv_bytes = issues_to_csv(fake_issues)
lines = csv_bytes.decode().strip().split("\n")
check("issues csv header",    lines[0], "row,column,code,message")
check("issues csv row count", len(lines), 3)

wr = wrong_rows_to_csv(df_exp, fake_issues).decode().strip().split("\n")
check("wrong_rows 2 data rows",    len(wr) - 1, 2)
check("wrong_rows row0 first val", wr[1].split(",")[0], "x")
check("wrong_rows row2 first val", wr[2].split(",")[0], "z")

# ── 15. STATS HELPERS ────────────────────────────────────────────────────────
ibr, icc = build_invalid_index(fake_issues)
check("index row0 A code",    ibr.get(0, {}).get("A"), ("TYPE_MISMATCH",  "bad"))
check("index row2 B code",    ibr.get(2, {}).get("B"), ("REQUIRED_EMPTY", "empty"))
check("invalid_count A",      icc.get("A"), 1)
check("invalid_row_set",      get_invalid_row_set(fake_issues), {0, 2})

# ── 16. EMPTY DATAFRAME ───────────────────────────────────────────────────────
df_empty = pd.DataFrame({"A": pd.Series([], dtype=str)})
schema_empty = build_manual_schema("e", ["A"], {"A": "integer"})
re = run_validation(df_empty, schema_empty)
check("empty df rows",         re["stats"]["rows"],         0)
check("empty df invalid_cells",re["stats"]["invalid_cells"],0)

# ── 17. NEW EDGE CASES ───────────────────────────────────────────────────────

# 17a. Multi-rule string violations — both codes returned, not short-circuited
col_multi = {"type": "string", "required": False, "nullable": True,
             "rules": {"min_len": 5, "regex": r"^[A-Z]+"}}
got_multi = sorted(codes(validate_cell("ab", col_multi, [])))
check("multi-rule both codes",  got_multi, sorted(["MIN_LEN_VIOLATED", "REGEX_MISMATCH"]))

# max_len + regex both violated
col_multi2 = {"type": "string", "required": False, "nullable": True,
              "rules": {"max_len": 2, "regex": r"^\d+"}}
got_multi2 = sorted(codes(validate_cell("abcde", col_multi2, [])))
check("multi-rule max+regex",   got_multi2, sorted(["MAX_LEN_VIOLATED", "REGEX_MISMATCH"]))

# 17b. Schema-level null_values respected end-to-end through run_validation
df_nulltok = pd.DataFrame({"X": ["hello", "MISSING", "world"]})
schema_nulltok = build_manual_schema("n", ["X"], {"X": "string"}, {"X": "error"})
schema_nulltok["null_values"] = ["MISSING"]
rv_nulltok = run_validation(df_nulltok, schema_nulltok)
check("schema null_values REQUIRED_EMPTY",
      any(ci["code"] == "REQUIRED_EMPTY" and ci["row"] == 1
          for ci in rv_nulltok["cell_issues"]), True)
check("schema null_values valid rows clean",
      len([ci for ci in rv_nulltok["cell_issues"] if ci["row"] != 1]), 0)

# 17c. "42.0" rejected by integer (whole float — common gotcha)
check("int 42.0 fail",   codes(validate_cell("42.0",  col_int, [])), ["TYPE_MISMATCH"])
check("int +5 ok",        codes(validate_cell("+5",    col_int, [])), [])

# 17d. Whitespace-padded values pass type checks after strip()
check("int whitespace ok",  codes(validate_cell("  42  ",   col_int,     [])), [])
check("num whitespace ok",  codes(validate_cell("  3.14  ", col_num,     [])), [])
check("bool whitespace ok", codes(validate_cell("  true  ", col_bool,    [])), [])
check("enum whitespace ok", codes(validate_cell("  RED  ",  col_enum,    [])), [])

# 17e. Enum with empty allowed list — every non-null value must fail
col_enum_empty = {"type": "enum", "required": False, "nullable": True,
                  "rules": {"allowed": []}}
check("enum empty allowed fails",    codes(validate_cell("anything", col_enum_empty, [])), ["ENUM_NOT_ALLOWED"])
check("enum empty allowed null ok",  codes(validate_cell("",         col_enum_empty, [])), [])

# 17f. Export edge cases — zero issues
empty_issues = {"cell_issues": []}
csv_empty_lines = issues_to_csv(empty_issues).decode().strip().split("\n")
check("issues csv zero issues header only", csv_empty_lines, ["row,column,code,message"])

wr_empty = wrong_rows_to_csv(df_exp, empty_issues).decode().strip().split("\n")
check("wrong_rows zero issues header only", len(wr_empty), 1)

# ── 18. NULL TOKEN EDGE CASES ────────────────────────────────────────────────
# Whitespace around a null token → still null
check("null  NA  req",      codes(validate_cell("  NA  ", col_req, [])), ["REQUIRED_EMPTY"])
check("null  NA  opt",      codes(validate_cell("  NA  ", col_opt, [])), [])
# Mixed-case null tokens → null (is_null_value is case-insensitive)
check("null Na req",        codes(validate_cell("Na",     col_req, [])), ["REQUIRED_EMPTY"])
check("null nUlL req",      codes(validate_cell("nUlL",   col_req, [])), ["REQUIRED_EMPTY"])
check("null N/A upper req", codes(validate_cell("N/A",    col_req, [])), ["REQUIRED_EMPTY"])
# "None" is NOT a null token — treated as a literal string value
col_req_str = {"type": "string", "required": True, "nullable": False, "rules": {}}
check("None is not null",   codes(validate_cell("None",   col_req_str, [])), [])
# pandas NaN renders as "nan" — NOT a null token, parsed as number → TYPE_MISMATCH
check("nan str not null num", codes(validate_cell("nan", col_num, [])), ["TYPE_MISMATCH"])
check("nan str not null int", codes(validate_cell("nan", col_int, [])), ["TYPE_MISMATCH"])
# Custom null token case-insensitive match
check("custom null MISSING upper", codes(validate_cell("MISSING", col_req, ["missing"])), ["REQUIRED_EMPTY"])
check("custom null lower match",   codes(validate_cell("missing", col_req, ["MISSING"])), ["REQUIRED_EMPTY"])

# ── 19. INTEGER EDGE CASES ───────────────────────────────────────────────────
# Scientific notation — int() cannot parse these
check("int 1e3 fail",       codes(validate_cell("1e3",   col_int, [])), ["TYPE_MISMATCH"])
check("int 1E6 fail",       codes(validate_cell("1E6",   col_int, [])), ["TYPE_MISMATCH"])
# Hex strings — int() with base 10 cannot parse these
check("int 0xFF fail",      codes(validate_cell("0xFF",  col_int, [])), ["TYPE_MISMATCH"])
# Explicit positive sign — Python int() accepts "+42"
check("int +42 ok",         codes(validate_cell("+42",   col_int, [])), [])
# Exact boundary: min == value == max → passes
col_int_exact = {"type": "integer", "required": False, "nullable": True, "rules": {"min": 5, "max": 5}}
check("int exact boundary ok", codes(validate_cell("5",  col_int_exact, [])), [])
# Just outside boundary
check("int exact boundary lo", codes(validate_cell("4",  col_int_exact, [])), ["MIN_VIOLATED"])
check("int exact boundary hi", codes(validate_cell("6",  col_int_exact, [])), ["MAX_VIOLATED"])
# Negative zero
check("int -0 ok",          codes(validate_cell("-0",    col_int, [])), [])
# Large integer (Python handles arbitrary precision)
check("int large ok",       codes(validate_cell("999999999999999999", col_int, [])), [])

# ── 20. NUMBER EDGE CASES ────────────────────────────────────────────────────
# Overflow to infinity
check("num 1e309 inf fail", codes(validate_cell("1e309",   col_num, [])), ["TYPE_MISMATCH"])
check("num -1e309 fail",    codes(validate_cell("-1e309",  col_num, [])), ["TYPE_MISMATCH"])
# Explicit positive sign
check("num +3.14 ok",       codes(validate_cell("+3.14",   col_num, [])), [])
# Comma-formatted numbers — float() cannot parse these
check("num 1,000 fail",     codes(validate_cell("1,000",   col_num, [])), ["TYPE_MISMATCH"])
check("num 1,000.5 fail",   codes(validate_cell("1,000.5", col_num, [])), ["TYPE_MISMATCH"])
# -0.0 vs min=0.0 — Python: -0.0 == 0.0 is True, -0.0 < 0.0 is False → passes
check("num -0.0 min ok",    codes(validate_cell("-0.0", col_num_rng, [])), [])
# Integer-like string valid for number type
check("num 42 ok",          codes(validate_cell("42",       col_num, [])), [])
# Whitespace + number (strip before parse)
check("num  42.5  ok",      codes(validate_cell(" 42.5 ",   col_num, [])), [])

# ── 21. BOOLEAN EDGE CASES ───────────────────────────────────────────────────
# All-caps variants not covered in section 5
for v in ["FALSE", "YES", "NO"]:
    check(f"bool {v} ok", codes(validate_cell(v, col_bool, [])), [])
# All-lowercase variants
for v in ["no", "yes"]:
    check(f"bool {v} ok", codes(validate_cell(v, col_bool, [])), [])
# Whitespace-padded boolean — already in 17d, skip duplicate
# Unambiguous rejects
check("bool on fail",   codes(validate_cell("on",  col_bool, [])), ["TYPE_MISMATCH"])
check("bool off fail",  codes(validate_cell("off", col_bool, [])), ["TYPE_MISMATCH"])

# ── 22. DATE EDGE CASES ──────────────────────────────────────────────────────
col_ymd = {"type": "date", "required": False, "nullable": True,
           "rules": {"formats": ["%Y-%m-%d"]}}
# Valid leap year date
check("date leap 2024 ok",        codes(validate_cell("2024-02-29", col_ymd, [])), [])
# Invalid leap year date (2023 is not a leap year)
check("date leap 2023 fail",      codes(validate_cell("2023-02-29", col_ymd, [])), ["DATE_PARSE_FAILED"])
# Invalid month
check("date month 13 fail",       codes(validate_cell("2024-13-01", col_ymd, [])), ["DATE_PARSE_FAILED"])
# Invalid day 0
check("date day 0 fail",          codes(validate_cell("2024-01-00", col_ymd, [])), ["DATE_PARSE_FAILED"])
# Multiple formats — first fails, second succeeds
col_multifmt = {"type": "date", "required": False, "nullable": True,
                "rules": {"formats": ["%d/%m/%Y", "%Y-%m-%d"]}}
check("date multifmt second ok",  codes(validate_cell("2024-01-31", col_multifmt, [])), [])
check("date multifmt first ok",   codes(validate_cell("31/01/2024", col_multifmt, [])), [])
check("date multifmt none fail",  codes(validate_cell("Jan 31 2024", col_multifmt, [])), ["DATE_PARSE_FAILED"])
# Trailing garbage in otherwise valid date string
check("date trailing garbage",    codes(validate_cell("2024-01-31X", col_ymd, [])), ["DATE_PARSE_FAILED"])

# ── 23. STRING RULE EDGE CASES ───────────────────────────────────────────────
# Bad regex pattern → silently ignored (re.error caught), value passes regex check
col_bad_re = {"type": "string", "required": False, "nullable": True,
              "rules": {"regex": r"[invalid(("}}
check("bad regex ignored",        codes(validate_cell("anything", col_bad_re, [])), [])
# min_len = 0 → every non-empty string passes
col_min0 = {"type": "string", "required": False, "nullable": True, "rules": {"min_len": 0}}
check("min_len 0 single char ok", codes(validate_cell("x",   col_min0, [])), [])
# max_len equal to string length → exactly at limit is OK
col_exact_len = {"type": "string", "required": False, "nullable": True, "rules": {"max_len": 3}}
check("max_len exact ok",         codes(validate_cell("abc", col_exact_len, [])), [])
check("max_len exact+1 fail",     codes(validate_cell("abcd",col_exact_len, [])), ["MAX_LEN_VIOLATED"])
# Strip applied before rule checks: "  AB  " → "AB" for regex and len
col_strip_rules = {"type": "string", "required": False, "nullable": True,
                   "rules": {"min_len": 2, "regex": r"^[A-Z]+"}}
check("strip before rules",       codes(validate_cell("  AB  ", col_strip_rules, [])), [])
# text type follows identical rule path
col_text_rules = {"type": "text", "required": False, "nullable": True,
                  "rules": {"min_len": 3, "max_len": 5}}
check("text min_len fail",        codes(validate_cell("ab",    col_text_rules, [])), ["MIN_LEN_VIOLATED"])
check("text max_len fail",        codes(validate_cell("abcdef",col_text_rules, [])), ["MAX_LEN_VIOLATED"])
check("text range ok",            codes(validate_cell("abcd",  col_text_rules, [])), [])

# ── 24. ENUM EDGE CASES ──────────────────────────────────────────────────────
# Space inside value is NOT stripped for enum content match
col_enum3 = {"type": "enum", "required": False, "nullable": True,
             "rules": {"allowed": ["RED", "GREEN", "BLUE"]}}
check("enum value with space fail",codes(validate_cell("RED GREEN", col_enum3, [])), ["ENUM_NOT_ALLOWED"])
# Numeric string in allowed list
col_enum_num = {"type": "enum", "required": False, "nullable": True,
                "rules": {"allowed": ["1", "2", "3"]}}
check("enum numeric allowed ok",  codes(validate_cell("2",   col_enum_num, [])), [])
check("enum numeric not in fail", codes(validate_cell("4",   col_enum_num, [])), ["ENUM_NOT_ALLOWED"])
# Whitespace-padded enum value — strip() applied before check
check("enum whitespace stripped ok", codes(validate_cell(" GREEN ", col_enum3, [])), [])

# ── 25. UNIQUE EDGE CASES ────────────────────────────────────────────────────
# Case normalization: "ABC" then "abc" → duplicate
df_uniq2 = pd.DataFrame({"K": ["ABC", "abc", "XYZ"]})
schema_uniq2 = build_manual_schema("u2", ["K"], {"K": "string"})
schema_uniq2["columns"]["K"]["rules"]    = {"unique": True}
schema_uniq2["columns"]["K"]["nullable"] = True
rv_uniq2 = run_validation(df_uniq2, schema_uniq2)
uniq2_rows = {(ci["row"], ci["code"]) for ci in rv_uniq2["cell_issues"]}
check("unique case dup row1 flagged",  (1, "UNIQUE_VIOLATED") in uniq2_rows, True)
check("unique case row0 not flagged",  (0, "UNIQUE_VIOLATED") in uniq2_rows, False)
check("unique case row2 not flagged",  (2, "UNIQUE_VIOLATED") in uniq2_rows, False)

# Three identical values: first OK, second and third flagged
df_uniq3 = pd.DataFrame({"V": ["X", "X", "X"]})
schema_uniq3 = build_manual_schema("u3", ["V"], {"V": "string"})
schema_uniq3["columns"]["V"]["rules"]    = {"unique": True}
schema_uniq3["columns"]["V"]["nullable"] = True
rv_uniq3 = run_validation(df_uniq3, schema_uniq3)
uniq3_codes = {(ci["row"], ci["code"]) for ci in rv_uniq3["cell_issues"]}
check("unique triple row0 clean",  (0, "UNIQUE_VIOLATED") in uniq3_codes, False)
check("unique triple row1 flagged",(1, "UNIQUE_VIOLATED") in uniq3_codes, True)
check("unique triple row2 flagged",(2, "UNIQUE_VIOLATED") in uniq3_codes, True)

# Single row — unique constraint never fires
df_uniq1 = pd.DataFrame({"W": ["only"]})
schema_uniq1 = build_manual_schema("u1", ["W"], {"W": "string"})
schema_uniq1["columns"]["W"]["rules"]    = {"unique": True}
schema_uniq1["columns"]["W"]["nullable"] = True
rv_uniq1 = run_validation(df_uniq1, schema_uniq1)
check("unique single row clean", rv_uniq1["cell_issues"], [])

# ── 26. SCHEMA MATCH EDGE CASES ──────────────────────────────────────────────
# Template with no columns → everything is "extra"
tmpl_empty_cols = {"id": "ec", "name": "EC", "columns": []}
res_ec = match_template_to_columns(tmpl_empty_cols, ["A", "B"])
check("empty tmpl matched",    res_ec["matched"],    {})
check("empty tmpl missing",    res_ec["missing"],    [])
check("empty tmpl unresolved", res_ec["unresolved"], [])
check("empty tmpl extra",      res_ec["extra"],      ["A", "B"])

# No file columns → all template cols unresolved/missing
res_nf = match_template_to_columns(tmpl_m, [])
check("no file cols matched",    res_nf["matched"],  {})
check("no file cols missing",    len(res_nf["missing"]),    2)
check("no file cols unresolved", len(res_nf["unresolved"]), 2)
check("no file cols extra",      res_nf["extra"],    [])

# Column name whitespace normalisation (normalize_column_name strips)
tmpl_ws = {"id": "ws", "name": "WS", "columns": [
    {"name": "MyCol", "type": "string", "required": True, "nullable": True, "rules": {}}
]}
res_ws = match_template_to_columns(tmpl_ws, ["  mycol  "])
check("col name ws match", res_ws["matched"].get("MyCol"), "  mycol  ")

# ── 27. PANDAS NaN IN run_validation ─────────────────────────────────────────
# str(float('nan')) = "nan" — NOT a null token; integer col → TYPE_MISMATCH
df_pandanan = pd.DataFrame({"N": [float("nan"), "42"]})
schema_pandanan = build_manual_schema("pn", ["N"], {"N": "integer"})
rv_pnan = run_validation(df_pandanan, schema_pandanan)
pnan_codes = {(ci["row"], ci["code"]) for ci in rv_pnan["cell_issues"]}
check("pandas nan row0 TYPE_MISMATCH", (0, "TYPE_MISMATCH") in pnan_codes, True)
check("pandas nan row1 clean",         (1, "TYPE_MISMATCH") in pnan_codes, False)

# ── 28. UNVALIDATED COLUMNS IN run_validation ─────────────────────────────────
df_unval = pd.DataFrame({"good": ["42"], "extra": ["not-an-int"]})
schema_unval = build_manual_schema("uv", ["good", "extra"], {"good": "integer", "extra": "integer"})
schema_unval["columns"]["extra"]["unvalidated"] = True
rv_unval = run_validation(df_unval, schema_unval)
check("unvalidated col not flagged",
      any(ci["column"] == "extra" for ci in rv_unval["cell_issues"]), False)
check("validated col still checked",
      len(rv_unval["cell_issues"]), 0)  # "42" is valid integer

# ── 29. STATS EDGE CASES ─────────────────────────────────────────────────────
# Empty issues → empty dicts
ibr_empty, icc_empty = build_invalid_index({"cell_issues": []})
check("build_invalid_index empty row dict",  ibr_empty, {})
check("build_invalid_index empty col dict",  icc_empty, {})
check("get_invalid_row_set empty",           get_invalid_row_set({"cell_issues": []}), set())

# Multiple issues on same cell → row appears once in invalid_row_set
multi_cell_issues = {"cell_issues": [
    {"row": 0, "column": "A", "code": "MIN_LEN_VIOLATED", "message": "x"},
    {"row": 0, "column": "A", "code": "REGEX_MISMATCH",   "message": "x"},
]}
check("invalid_row_set deduped",  get_invalid_row_set(multi_cell_issues), {0})
ibr_mc, icc_mc = build_invalid_index(multi_cell_issues)
# build_invalid_index keeps last code written for same (row, col)
check("build_invalid_index last code wins", ibr_mc[0]["A"][0], "REGEX_MISMATCH")
check("build_invalid_index col count 1",    icc_mc.get("A"), 1)

# ── 30. run_validation WORST_COLUMN LOGIC ────────────────────────────────────
# worst_col is None when all columns are valid
df_allvalid = pd.DataFrame({"A": ["1", "2"], "B": ["x", "y"]})
schema_allvalid = build_manual_schema("av", ["A", "B"],
    {"A": "integer", "B": "string"})
rv_av = run_validation(df_allvalid, schema_allvalid)
check("worst_col None when all valid", rv_av["stats"]["worst_column"], None)

# worst_col correctly identifies column with most errors
df_worst = pd.DataFrame({"A": ["bad","bad","bad"], "B": ["bad","ok","ok"]})
schema_worst = build_manual_schema("wc", ["A", "B"],
    {"A": "integer", "B": "integer"})
rv_worst = run_validation(df_worst, schema_worst)
check("worst_col is A (3 errors vs 1)", rv_worst["stats"]["worst_column"], "A")

# ── 31. MISSING COLUMN IN dataset_issues ─────────────────────────────────────
# Build schema manually: Required1 is in schema but NOT in the DataFrame.
# build_resolved_schema only adds columns that have a file mapping, so we
# must place the required column in the schema dict ourselves to test the
# run_validation MISSING_COLUMN detection path.
schema_miss = {
    "id": "ms", "dataset_id": "ms",
    "strict": False, "null_values": [],
    "columns": {
        "Required1": {
            "type": "string", "required": True,  "nullable": True,
            "unvalidated": False, "rules": {}
        },
        "Optional1": {
            "type": "string", "required": False, "nullable": True,
            "unvalidated": False, "rules": {}
        },
    }
}
rv_miss = run_validation(pd.DataFrame({"SomethingElse": ["a"]}), schema_miss)
di_miss_codes = {d["code"] for d in rv_miss["dataset_issues"]}
check("MISSING_COLUMN required",  "MISSING_COLUMN" in di_miss_codes, True)
# Optional missing column must NOT be raised as MISSING_COLUMN
missing_col_names = {d["column"] for d in rv_miss["dataset_issues"] if d["code"] == "MISSING_COLUMN"}
check("MISSING_COLUMN optional not raised", "Optional1" in missing_col_names, False)

# ── 32. build_resolved_schema UNVALIDATED EXTRAS ─────────────────────────────
tmpl_res = {
    "id": "br", "name": "BR", "strict": False, "null_values": [],
    "columns": [
        {"name": "A", "type": "integer", "required": False, "nullable": True, "rules": {}}
    ]
}
schema_res = build_resolved_schema(tmpl_res, {"A": "A"}, ["A", "ExtraCol"])
check("resolved extra col unvalidated",  schema_res["columns"]["ExtraCol"]["unvalidated"], True)
check("resolved mapped col not unvalidated", schema_res["columns"]["A"].get("unvalidated", False), False)

# ── 33. DATE MIN / MAX RULES ─────────────────────────────────────────────────
col_dated = {"type": "date", "required": False, "nullable": True,
             "rules": {"formats": ["%Y-%m-%d"],
                       "min_date": "2020-01-01", "max_date": "2024-12-31"}}
check("date min ok",           codes(validate_cell("2022-06-15", col_dated, [])), [])
check("date below min",        codes(validate_cell("2019-12-31", col_dated, [])), ["MIN_VIOLATED"])
check("date above max",        codes(validate_cell("2025-01-01", col_dated, [])), ["MAX_VIOLATED"])
check("date exactly at min",   codes(validate_cell("2020-01-01", col_dated, [])), [])
check("date exactly at max",   codes(validate_cell("2024-12-31", col_dated, [])), [])
# Custom boundary format
col_dated_fmt = {"type": "date", "required": False, "nullable": True,
                 "rules": {"formats": ["%d.%m.%Y"],
                           "min_date": "01.01.2020", "max_date": "31.12.2024",
                           "date_format": "%d.%m.%Y"}}
check("date custom fmt min ok",  codes(validate_cell("15.06.2022", col_dated_fmt, [])), [])
check("date custom fmt below",   codes(validate_cell("31.12.2019", col_dated_fmt, [])), ["MIN_VIOLATED"])
# Invalid min_date string → silently ignored (no crash)
col_dated_bad = {"type": "date", "required": False, "nullable": True,
                 "rules": {"formats": ["%Y-%m-%d"], "min_date": "not-a-date"}}
check("date bad min_date ignored", codes(validate_cell("2022-01-01", col_dated_bad, [])), [])

# ── 34. NOT_IN BLOCKLIST ─────────────────────────────────────────────────────
col_not_in_str = {"type": "string", "required": False, "nullable": True,
                  "rules": {"not_in": ["ADMIN", "ROOT", "SYSTEM"]}}
check("not_in blocked",         codes(validate_cell("ADMIN",   col_not_in_str, [])), ["NOT_IN_VIOLATED"])
check("not_in case insensitive",codes(validate_cell("admin",   col_not_in_str, [])), ["NOT_IN_VIOLATED"])
check("not_in allowed",         codes(validate_cell("alice",   col_not_in_str, [])), [])
check("not_in null skipped",    codes(validate_cell("",        col_not_in_str, [])), [])
# Works on integer type too
col_not_in_int = {"type": "integer", "required": False, "nullable": True,
                  "rules": {"not_in": ["0", "999"]}}
check("not_in int blocked",     codes(validate_cell("0",   col_not_in_int, [])), ["NOT_IN_VIOLATED"])
check("not_in int allowed",     codes(validate_cell("42",  col_not_in_int, [])), [])
# Combined with other rules
col_not_in_combo = {"type": "string", "required": False, "nullable": True,
                    "rules": {"min_len": 5, "not_in": ["ADMIN"]}}
check("not_in + min_len both",  sorted(codes(validate_cell("ADM", col_not_in_combo, []))),
      ["MIN_LEN_VIOLATED"])   # "ADM" too short but NOT in blocklist → only min_len fires
check("not_in fires alone",     codes(validate_cell("ADMIN", col_not_in_combo, [])),
      ["NOT_IN_VIOLATED"])     # "ADMIN" passes min_len but hits not_in

# ── 35. UNIQUE_TOGETHER ───────────────────────────────────────────────────────
df_ut = pd.DataFrame({
    "A": ["x", "x", "x", "y"],
    "B": ["1", "1", "2", "1"],
})
schema_ut = build_manual_schema("ut", ["A", "B"], {"A": "string", "B": "string"})
schema_ut["dataset_rules"] = [{"type": "unique_together", "columns": ["A", "B"]}]
rv_ut = run_validation(df_ut, schema_ut)
ut_rows = {(ci["row"], ci["code"]) for ci in rv_ut["cell_issues"]}
# row 0: ("x","1") first → OK
check("ut row0 clean",  (0, "UNIQUE_TOGETHER_VIOLATED") in ut_rows, False)
# row 1: ("x","1") duplicate → flagged for both columns
check("ut row1 A flagged", (1, "UNIQUE_TOGETHER_VIOLATED") in ut_rows, True)
check("ut row1 B flagged",
      any(ci["row"]==1 and ci["column"]=="B" and ci["code"]=="UNIQUE_TOGETHER_VIOLATED"
          for ci in rv_ut["cell_issues"]), True)
# row 2: ("x","2") new → OK
check("ut row2 clean",  (2, "UNIQUE_TOGETHER_VIOLATED") in ut_rows, False)
# row 3: ("y","1") new → OK
check("ut row3 clean",  (3, "UNIQUE_TOGETHER_VIOLATED") in ut_rows, False)
# All-null rows must not be flagged
df_ut_null = pd.DataFrame({"A": ["", ""], "B": ["", ""]})
schema_ut_null = build_manual_schema("utn", ["A", "B"], {"A": "string", "B": "string"})
schema_ut_null["dataset_rules"] = [{"type": "unique_together", "columns": ["A", "B"]}]
rv_ut_null = run_validation(df_ut_null, schema_ut_null)
check("ut all-null rows not flagged", rv_ut_null["cell_issues"], [])
# dataset_rules with fewer than 2 available columns → no crash, no issues
schema_ut_bad = build_manual_schema("utb", ["A"], {"A": "string"})
schema_ut_bad["dataset_rules"] = [{"type": "unique_together", "columns": ["A", "MISSING"]}]
rv_ut_bad = run_validation(pd.DataFrame({"A": ["x", "x"]}), schema_ut_bad)
check("ut missing col no crash",
      all(ci["code"] != "UNIQUE_TOGETHER_VIOLATED" for ci in rv_ut_bad["cell_issues"]), True)

# ── 36. NUMERIC STATS IN run_validation ──────────────────────────────────────
df_ns = pd.DataFrame({
    "Age":   ["20", "30", "40", "bad"],
    "Score": ["0.5", "1.0", "1.5", "2.0"],
})
schema_ns = build_manual_schema("ns", ["Age", "Score"],
    {"Age": "integer", "Score": "number"})
rv_ns = run_validation(df_ns, schema_ns)
age_stats   = rv_ns["column_summary"]["Age"].get("numeric_stats", {})
score_stats = rv_ns["column_summary"]["Score"].get("numeric_stats", {})
# Age: valid rows are 20,30,40 ("bad" fails type check → excluded from stats)
check("numeric stats Age count",  age_stats.get("count"),  3)
check("numeric stats Age min",    age_stats.get("min"),    20.0)
check("numeric stats Age max",    age_stats.get("max"),    40.0)
check("numeric stats Age mean",   age_stats.get("mean"),   30.0)
check("numeric stats std present",  "std" in age_stats, True)
# Score: all 4 valid
check("numeric stats Score count", score_stats.get("count"), 4)
check("numeric stats Score min",   score_stats.get("min"),   0.5)
check("numeric stats Score max",   score_stats.get("max"),   2.0)
# String columns must NOT have numeric_stats
schema_str = build_manual_schema("nss", ["Name"], {"Name": "string"})
rv_str = run_validation(pd.DataFrame({"Name": ["Alice"]}), schema_str)
check("no numeric stats for string col",
      "numeric_stats" in rv_str["column_summary"].get("Name", {}), False)

# ── 37. CSV DELIMITER AUTO-DETECTION ─────────────────────────────────────────
import tempfile, os as _os
# Write a semicolon-delimited file and check it parses correctly
_sc = "Name;Age;City\nAlice;30;London\nBob;25;Paris\n"
with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as _tf:
    _tf.write(_sc)
    _tmp_csv = _tf.name
try:
    from core.ingestion import ingest_csv
    df_sc, meta_sc = ingest_csv(_tmp_csv)
    check("semicolon delimiter detected", meta_sc.get("delimiter"), ";")
    check("semicolon 3 columns",          len(df_sc.columns),        3)
    check("semicolon col names ok",       list(df_sc.columns),       ["Name", "Age", "City"])
    check("semicolon 2 rows",             len(df_sc),                 2)
finally:
    _os.unlink(_tmp_csv)

# ── 38. JSON INGEST ───────────────────────────────────────────────────────────
from core.ingestion import ingest_json
import tempfile, json as _json_mod

# Top-level array
_json_arr = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as _tf:
    _json_mod.dump(_json_arr, _tf)
    _tmp_json = _tf.name
try:
    df_ja, meta_ja = ingest_json(_tmp_json)
    check("json array rows",   len(df_ja), 2)
    check("json array cols",   list(df_ja.columns), ["id", "name"])
    check("json array value",  df_ja["name"].iloc[0], "Alice")
finally:
    _os.unlink(_tmp_json)

# Wrapped object {"data": [...]}
_json_wrap = {"data": [{"x": "10"}, {"x": "20"}]}
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as _tf:
    _json_mod.dump(_json_wrap, _tf)
    _tmp_json2 = _tf.name
try:
    df_jw, meta_jw = ingest_json(_tmp_json2)
    check("json wrapped rows", len(df_jw), 2)
    check("json wrapped col",  list(df_jw.columns), ["x"])
finally:
    _os.unlink(_tmp_json2)

# ── 39. EXCEL EXPORT ─────────────────────────────────────────────────────────
from core.export import issues_to_xlsx
_df_xl = pd.DataFrame({"A": ["hello", "world"], "B": ["1", "bad"]})
_xl_issues = {"cell_issues": [
    {"row": 1, "column": "B", "code": "TYPE_MISMATCH", "message": "bad"},
]}
_xl_bytes = issues_to_xlsx(_df_xl, _xl_issues)
check("xlsx export non-empty",  len(_xl_bytes) > 0, True)
check("xlsx is valid zip magic", _xl_bytes[:2], b'PK')  # xlsx = zip archive

# ─────────────────────────────────────────────────────────────────────────────
print()
total = PASS + FAIL
print(f"  {PASS}/{total} passed", "" if FAIL == 0 else f"  ({FAIL} FAILED)")
sys.exit(1 if FAIL else 0)
