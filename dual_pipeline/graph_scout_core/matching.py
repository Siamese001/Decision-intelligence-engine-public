"""String normalization and fuzzy similarity matching for Graph Scout."""
from __future__ import annotations

import difflib
import re

# Stopwords and tokens ignored during name normalization
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "by", "with",
    "project", "initiative", "program", "team", "workstream", "phase", "stage",
})


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy comparison."""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", name.lower())
    tokens = [t for t in cleaned.split() if t and t not in _STOPWORDS]
    return " ".join(tokens) if tokens else cleaned.strip()


def _token_jaccard(name_a: str, name_b: str) -> float:
    """Compute token Jaccard similarity."""
    set_a = set(_normalize_name(name_a).split())
    set_b = set(_normalize_name(name_b).split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# Common nickname mappings for person/stakeholder entity matching
_NICKNAMES: dict[str, set[str]] = {
    "matt": {"matthew"}, "matthew": {"matt"},
    "jake": {"jacob"}, "jacob": {"jake"},
    "dan": {"daniel"}, "daniel": {"dan"},
    "alex": {"alexander", "alexandra"}, "alexander": {"alex"}, "alexandra": {"alex"},
    "ed": {"eduardo", "edward"}, "eduardo": {"ed"}, "edward": {"ed"},
    "rich": {"richard"}, "richard": {"rich"},
    "bill": {"william"}, "william": {"bill"},
    "bob": {"robert", "rob"}, "rob": {"robert", "bob"}, "robert": {"bob", "rob"},
    "mike": {"michael"}, "michael": {"mike"},
    "dave": {"david"}, "david": {"dave"},
    "chris": {"christopher"}, "christopher": {"chris"},
    "steve": {"stephen", "steven"}, "stephen": {"steve"}, "steven": {"steve"},
    "greg": {"gregory"}, "gregory": {"greg"},
    "tom": {"thomas"}, "thomas": {"tom"},
    "jen": {"jennifer"}, "jennifer": {"jen"},
    "tony": {"anthony"}, "anthony": {"tony"},
    "mandy": {"amanda"}, "amanda": {"mandy"},
    "stepfan": {"stefanos", "steph"}, "stefanos": {"stepfan", "steph"}, "steph": {"stefanos", "stepfan"},
}

_PERSON_HONORIFICS = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "mba", "pmp", "mr", "ms", "mrs", "dr"})
_NON_PERSON_TOKENS = frozenset({
    "sprint", "hub", "platform", "engine", "pipeline", "system", "model", "portal",
    "tool", "dashboard", "report", "source", "document", "meeting", "charter", "sow",
    "plan", "architecture", "governance", "integration", "consolidation", "migration",
    "automation", "analytics", "insights", "vision", "billing", "journey", "discover",
    "prototype", "legacy", "framework", "service", "services", "module", "agent",
    "scope", "requirement", "requirements", "supplier", "rule", "rules", "oracle",
    "unifier", "primavera", "authoring", "asset", "protection", "handling",
})


_EXCLUSIVE_MODIFIER_TOKENS = frozenset({
    "admin", "uat", "staging", "sandbox", "pilot", "decommissioned",
    "capital", "total", "opex", "capex", "gross", "net",
    "vlm", "devin",
})

_ROLE_HEAD_NOUNS = frozenset({
    "president", "director", "manager", "architect", "partner", "owner",
    "engineer", "lead", "analyst", "consultant", "specialist", "developer",
    "designer", "coordinator", "administrator", "officer", "scrum", "coach",
})

_ROLE_EXCLUSIVE_TOKENS = frozenset({
    "lead", "chief", "principal", "senior", "junior", "associate", "assistant",
    "client", "service", "project", "program", "operations", "product", "infrastructure",
    "cloud", "devops", "data", "security", "platform", "ai", "ml", "qa", "test",
    "frontend", "backend", "fullstack", "systems", "network", "enterprise",
    "solution", "solutions", "technical", "business", "delivery", "governance",
    "maximo", "learning", "development", "nexus", "jarvis", "dociq", "sherpa", "apex_trans", "nea",
})


def _has_exclusive_token_mismatch(toks_a: set[str], toks_b: set[str], vocab: frozenset[str]) -> bool:
    return bool((toks_a & vocab) ^ (toks_b & vocab))


def _has_acronym_conflict(a: str, b: str) -> bool:
    """Detect distinct hyphenated or uppercase acronyms like AI-SDLC vs AI-DLC."""
    acronyms_a = {m.upper() for m in re.findall(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+\b", a)}
    acronyms_b = {m.upper() for m in re.findall(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+\b", b)}
    if acronyms_a and acronyms_b and acronyms_a != acronyms_b:
        return True
    return False


def _is_near_identical_surname(sa: str, sb: str) -> bool:
    if len(sa) < 4 or len(sb) < 4 or sa[0] != sb[0] or abs(len(sa) - len(sb)) > 1:
        return False
    if re.sub(r"(.)\1+", r"\1", sa) == re.sub(r"(.)\1+", r"\1", sb):
        return True
    from rapidfuzz import fuzz

    return (fuzz.ratio(sa, sb) / 100.0) >= 0.88


def _has_same_prefix_distinct_suffix(la: str, lb: str) -> bool:
    list_a = la.split()
    list_b = lb.split()
    if len(list_a) == 2 and len(list_b) == 2 and list_a[0] == list_b[0] and list_a[1] != list_b[1]:
        if _is_near_identical_surname(list_a[1], list_b[1]):
            return False
        return (
            list_a[1].isalpha()
            and list_b[1].isalpha()
            and list_a[1] not in _STOPWORDS
            and list_b[1] not in _STOPWORDS
        )
    return False


def _has_legacy_role_phrase_conflict(la: str, lb: str) -> bool:
    vp_conflict = ("vice president" in la) != ("vice president" in lb) and ("president" in la and "president" in lb)
    ed_conflict = ("executive director" in la) != ("executive director" in lb) and ("director" in la and "director" in lb)
    mgr_conflict = ("manager" in la) != ("manager" in lb) and ("director" in la or "director" in lb)
    return vp_conflict or ed_conflict or mgr_conflict


def has_conflicting_qualifiers(a: str, b: str) -> bool:
    """Return True if two names have mutually exclusive numbers, years, or seniority qualifiers."""
    la = a.lower()
    lb = b.lower()
    nums_a = set(re.findall(r"\b\d+(?:\.\d+)?\b", la))
    nums_b = set(re.findall(r"\b\d+(?:\.\d+)?\b", lb))
    if (nums_a and nums_b and nums_a != nums_b) or _has_acronym_conflict(a, b):
        return True

    toks_a = set(re.findall(r"[a-z0-9]+", la))
    toks_b = set(re.findall(r"[a-z0-9]+", lb))
    if len(toks_a) == 2 and len(toks_b) == 2 and _has_same_prefix_distinct_suffix(la, lb):
        return True
    if _has_exclusive_token_mismatch(toks_a, toks_b, _EXCLUSIVE_MODIFIER_TOKENS):
        return True

    is_role_like = bool((toks_a | toks_b) & _ROLE_HEAD_NOUNS)
    if is_role_like and _has_exclusive_token_mismatch(toks_a, toks_b, _ROLE_EXCLUSIVE_TOKENS):
        return True
    return _has_legacy_role_phrase_conflict(la, lb)


def has_description_conflict(desc_a: str, desc_b: str) -> bool:
    """Return True if node descriptions contain explicit do-not-merge guards or conflicting dollar figures."""
    da = (desc_a or "").lower()
    db = (desc_b or "").lower()
    if "do not merge" in da or "do not merge" in db:
        return True
    dollars_a = set(re.findall(r"\$\d+(?:\.\d+)?[mkb]?\b", da))
    dollars_b = set(re.findall(r"\$\d+(?:\.\d+)?[mkb]?\b", db))
    if dollars_a and dollars_b and dollars_a != dollars_b:
        return True
    return False


def _middle_tokens_compatible(mid_a: list[str], mid_b: list[str]) -> bool:
    if not mid_a or not mid_b or mid_a == mid_b:
        return True
    if len(mid_a) == 1 and len(mid_b) == 1 and (len(mid_a[0]) == 1 or len(mid_b[0]) == 1):
        return mid_a[0][0] == mid_b[0][0]
    return False


def _parse_person_name_parts(raw: str) -> tuple[set[str], list[str], str] | None:
    if re.search(r"\d", raw) or re.search(r"\b[A-Z]{2,}\b", raw):
        return None
    paren_nicks = [t for m in re.findall(r"\(([^)]+)\)", raw.lower()) for t in re.findall(r"[a-z]+", m)]
    stripped = re.sub(r"\([^)]*\)", " ", raw.lower())
    toks = [t for t in re.findall(r"[a-z]+", stripped) if t not in _PERSON_HONORIFICS]
    if len(toks) < 2 or len(toks) > 5 or len(toks[-1]) < 3:
        return None
    vocab_check = {t for t in (set(toks) | set(paren_nicks)) if len(t) > 1}
    if vocab_check & (_ROLE_HEAD_NOUNS | _ROLE_EXCLUSIVE_TOKENS | _NON_PERSON_TOKENS | _STOPWORDS):
        return None
    base_given = {toks[0]} | set(paren_nicks)
    expanded_given = set(base_given)
    for g in base_given:
        expanded_given.update(_NICKNAMES.get(g, set()))
    return expanded_given, toks[1:-1], toks[-1]


def _check_person_nickname_or_typo(raw_a: str, raw_b: str) -> float | None:
    """Detect matching person identity across parenthetical nicknames, middle names, and surname typos."""
    parts_a = _parse_person_name_parts(raw_a)
    parts_b = _parse_person_name_parts(raw_b)
    if parts_a is None or parts_b is None:
        return None
    given_a, mid_a, last_a = parts_a
    given_b, mid_b, last_b = parts_b
    if not (given_a & given_b):
        return None
    if not _middle_tokens_compatible(mid_a, mid_b):
        return 0.0
    if last_a == last_b:
        return 0.94 if (mid_a or mid_b) else 0.92
    if _is_near_identical_surname(last_a, last_b):
        return 0.92
    return None


def _string_similarity(a: str, b: str) -> float:
    """Compute combined Jaccard and SequenceMatcher similarity ratio with qualifier guards."""
    if has_conflicting_qualifiers(a, b):
        return 0.0

    norm_a = _normalize_name(a)
    norm_b = _normalize_name(b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0

    person_sim = _check_person_nickname_or_typo(a, b)
    if person_sim is not None:
        return person_sim

    from rapidfuzz import fuzz

    seq_ratio = fuzz.ratio(norm_a, norm_b) / 100.0
    jaccard = _token_jaccard(a, b)
    # Weight SequenceMatcher slightly higher for acronyms/abbreviations
    return round(0.55 * seq_ratio + 0.45 * jaccard, 4)
