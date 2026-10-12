"""
Moteur de formules sécurisé — v3.

Ajoute les fonctions les plus utilisées dans les vrais formulaires Kobo/XLSForm :
- if(condition, valeur_si_vrai, valeur_si_faux)  → variables dérivées, scores, tranches
- selected(champ, 'valeur')                       → "cette réponse a-t-elle été cochée
                                                       dans un choix multiples ?"
- coalesce(a, b, ...)                             → première valeur non vide

Contrairement aux fonctions mathématiques (pow, sqrt, abs, round), ces trois
fonctions ne doivent PAS convertir leurs arguments en nombre — if() doit pouvoir
renvoyer du texte, selected() doit recevoir une liste telle quelle. Elles sont
donc classées à part (GENERIC_FUNCTIONS) et reçoivent leurs arguments "bruts".

Sert à la fois pour :
- les champs "calculated" (résultat numérique ou texte, ex : IMC, tranche d'âge)
- les conditions d'affichage "relevant" (résultat vrai/faux, logique conditionnelle)
- les conditions d'alerte "alert_if" (résultat vrai/faux)

Toujours sans eval()/exec() : grammaire explicite, fonctions en liste blanche.
"""

import re
import math

NUMERIC_FUNCTIONS = {
    "pow": lambda a, b: math.pow(a, b),
    "sqrt": lambda a: math.sqrt(a),
    "abs": lambda a: abs(a),
    "round": lambda a, n=0: round(a, int(n)),
}


def _fn_if(condition, true_value, false_value):
    return true_value if _truthy(condition) else false_value


def _fn_selected(value, target):
    """Reproduit selected() de XLSForm : vrai si `target` fait partie des
    réponses cochées dans un champ "choix multiples" (une liste), ou si
    `value` est directement égal à `target` pour un champ à choix unique."""
    if isinstance(value, list):
        return str(target) in [str(v) for v in value]
    if value is None:
        return False
    return str(value) == str(target)


def _fn_coalesce(*args):
    for a in args:
        if a is not None and a != "" and not (isinstance(a, list) and len(a) == 0):
            return a
    return args[-1] if args else None


GENERIC_FUNCTIONS = {
    "if": _fn_if,
    "selected": _fn_selected,
    "coalesce": _fn_coalesce,
}

_TOKEN_PATTERN = re.compile(
    r"""\s*(?:
        (\d+\.?\d*)
        |([a-zA-Z_][a-zA-Z0-9_]*)
        |'([^']*)'
        |"([^"]*)"
        |(==|!=|<=|>=)
        |([+\-*/(),<>])
    )""",
    re.VERBOSE,
)


class FormulaError(Exception):
    pass


def _tokenize(expr: str):
    tokens = []
    pos = 0
    while pos < len(expr):
        match = _TOKEN_PATTERN.match(expr, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"Caractère non autorisé près de : '{expr[pos:pos + 10]}'")
        number, ident, str1, str2, cmp2, op1 = match.groups()
        if number is not None:
            tokens.append(("number", float(number)))
        elif ident is not None:
            tokens.append(("ident", ident))
        elif str1 is not None:
            tokens.append(("string", str1))
        elif str2 is not None:
            tokens.append(("string", str2))
        elif cmp2 is not None:
            tokens.append(("op", cmp2))
        elif op1 is not None:
            tokens.append(("op", op1))
        pos = match.end()
    return tokens


def _to_number(v):
    if v is None:
        return float("nan")
    if isinstance(v, bool):
        raise FormulaError("Impossible d'utiliser une valeur oui/non dans un calcul numérique")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise FormulaError(f"Valeur non numérique utilisée dans un calcul : '{v}'")


def _try_to_number(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    return bool(v)


def _compare(op, left, right):
    # Un champ pas encore rempli (valeur manquante) ne peut jamais rendre vraie
    # une comparaison d'ordre (< > <= >=) — on ne peut pas dire si "vide" est
    # plus petit ou plus grand qu'un nombre. Seules l'égalité et la différence
    # restent définies dans ce cas (une valeur manquante n'égale jamais une
    # vraie valeur).
    if left is None or right is None:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        return False

    l_num = _try_to_number(left)
    r_num = _try_to_number(right)
    if l_num is not None and r_num is not None:
        l, r = l_num, r_num
    else:
        l, r = str(left), str(right)
    if op == "==":
        return l == r
    if op == "!=":
        return l != r
    if op == "<":
        return l < r
    if op == ">":
        return l > r
    if op == "<=":
        return l <= r
    if op == ">=":
        return l >= r
    raise FormulaError(f"Opérateur inconnu : {op}")


class _Parser:
    def __init__(self, tokens, values):
        self.tokens = tokens
        self.pos = 0
        self.values = values

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        t = self.peek()
        self.pos += 1
        return t

    def _resolve_ident(self, name):
        if name == "true":
            return True
        if name == "false":
            return False
        if name not in self.values:
            raise FormulaError(f"Champ inconnu dans la formule : {name}")
        v = self.values[name]
        if v == "":
            return None
        return v

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise FormulaError("Formule incomplète")

        if t[0] == "number":
            self.advance()
            return t[1]

        if t[0] == "string":
            self.advance()
            return t[1]

        if t[0] == "ident":
            if t[1] in ("and", "or", "not"):
                raise FormulaError(f"'{t[1]}' n'est pas attendu à cet endroit de la formule")
            self.advance()
            nxt = self.peek()
            if nxt and nxt[1] == "(":
                self.advance()
                args = []
                if self.peek() and self.peek()[1] != ")":
                    args.append(self.parse_or())
                    while self.peek() and self.peek()[1] == ",":
                        self.advance()
                        args.append(self.parse_or())
                if not self.peek() or self.peek()[1] != ")":
                    raise FormulaError("Parenthèse manquante")
                self.advance()
                if t[1] in NUMERIC_FUNCTIONS:
                    return NUMERIC_FUNCTIONS[t[1]](*[_to_number(a) for a in args])
                if t[1] in GENERIC_FUNCTIONS:
                    return GENERIC_FUNCTIONS[t[1]](*args)
                raise FormulaError(f"Fonction non autorisée : {t[1]}")
            return self._resolve_ident(t[1])

        if t[1] == "(":
            self.advance()
            v = self.parse_or()
            if not self.peek() or self.peek()[1] != ")":
                raise FormulaError("Parenthèse manquante")
            self.advance()
            return v

        if t[1] == "-":
            self.advance()
            return -_to_number(self.parse_primary())

        raise FormulaError("Formule invalide")

    def parse_mul_div(self):
        v = self.parse_primary()
        while self.peek() and self.peek()[1] in ("*", "/"):
            op = self.advance()[1]
            rhs = self.parse_primary()
            v = _to_number(v) * _to_number(rhs) if op == "*" else _to_number(v) / _to_number(rhs)
        return v

    def parse_add_sub(self):
        v = self.parse_mul_div()
        while self.peek() and self.peek()[1] in ("+", "-"):
            op = self.advance()[1]
            rhs = self.parse_mul_div()
            v = _to_number(v) + _to_number(rhs) if op == "+" else _to_number(v) - _to_number(rhs)
        return v

    def parse_comparison(self):
        v = self.parse_add_sub()
        if self.peek() and self.peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self.advance()[1]
            rhs = self.parse_add_sub()
            return _compare(op, v, rhs)
        return v

    def parse_not(self):
        if self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "not":
            self.advance()
            return not _truthy(self.parse_not())
        return self.parse_comparison()

    def parse_and(self):
        v = self.parse_not()
        while self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "and":
            self.advance()
            rhs = self.parse_not()
            v = _truthy(v) and _truthy(rhs)
        return v

    def parse_or(self):
        v = self.parse_and()
        while self.peek() and self.peek()[0] == "ident" and self.peek()[1] == "or":
            self.advance()
            rhs = self.parse_and()
            v = _truthy(v) or _truthy(rhs)
        return v


def evaluate_formula(formula: str, values: dict):
    """Évalue une formule et renvoie sa valeur (nombre, texte ou booléen selon le cas)."""
    tokens = _tokenize(formula)
    parser = _Parser(tokens, values)
    result = parser.parse_or()
    if parser.pos < len(tokens):
        raise FormulaError("Formule invalide (caractères en trop)")
    return result


def evaluate_condition(condition: str, values: dict, default: bool = True) -> bool:
    """Évalue une condition (relevant / alert_if) et renvoie un booléen.
    En cas d'erreur dans la formule (faute de frappe, champ inconnu...), renvoie
    `default` plutôt que de faire planter tout l'enregistrement du formulaire."""
    if not condition or not condition.strip():
        return True
    try:
        return _truthy(evaluate_formula(condition, values))
    except FormulaError:
        return default


def validate_formula_syntax(expr: str):
    """Vérifie qu'une formule/condition est syntaxiquement valide, sans l'évaluer
    contre de vraies données (utile pour valider un formulaire importé avant de
    l'enregistrer). Renvoie None si tout va bien, sinon un message d'erreur."""
    if not expr or not expr.strip():
        return None
    try:
        tokens = _tokenize(expr)
        fake_values = {}
        for t in tokens:
            if (t[0] == "ident" and t[1] not in ("and", "or", "not", "true", "false")
                    and t[1] not in NUMERIC_FUNCTIONS and t[1] not in GENERIC_FUNCTIONS):
                fake_values[t[1]] = "1"
        evaluate_formula(expr, fake_values)
        return None
    except FormulaError as e:
        return str(e)
    except Exception as e:
        return f"Erreur inattendue : {e}"
