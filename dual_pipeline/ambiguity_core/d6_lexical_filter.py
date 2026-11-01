"""Domain 6: Lexical filtering, common English vocabulary suppression, syntactic validation,
and evidence-based candidate gating for Domain 6 Entity Clarification.
Enforces Constraints C-12 (Anti-Hallucination Guard) and C-27 (Grounding Integrity).
"""

from __future__ import annotations

import re
from typing import Set

# Common non-person capitalized words, tools, and discourse markers to suppress
SUPPRESSED_WORDS: Set[str] = {
    # Days of week
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # Months
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    # Technologies, platforms, and corporate tools
    "partner_advisory", "apex_global", "google", "microsoft", "teams", "aws", "python", "sprint",
    "lens", "nexus", "atlas", "maximo", "devin", "jira", "slack", "strands", "kiro",
    "plumtree", "opentelemetry", "chatubity", "chatgpt", "gpt", "claude", "gemini",
    "github", "gitlab", "docker", "kubernetes", "linux", "zoom", "webex", "outlook",
    "excel", "powerpoint", "word", "confluence", "postgres", "mysql", "redis", "dynamo",
    "dynamodb", "s3", "lambda", "ec2", "api", "apis", "sdk", "cli", "ui", "uat", "qa",
    # Discourse markers, adverbs, and conversational fillers
    "yeah", "right", "well", "today", "yesterday", "tomorrow", "sure", "okay", "hello",
    "thanks", "thank", "actually", "basically", "probably", "certainly", "definitely",
    "regardless", "meanwhile", "however", "therefore", "furthermore", "additionally",
    "indeed", "perhaps", "maybe", "honestly", "frankly", "anyway", "anyways", "alright",
    "bye", "goodbye", "yes", "no", "yep", "nope", "please", "sorry", "pardon",
    # Generic participant labels and meeting constants
    "participant", "unknown", "speaker", "none", "null", "true", "false", "attendee",
    "host", "guest", "user", "caller", "operator", "team", "everyone", "folks", "people",
    "guys", "member", "members", "person", "persons", "group", "client", "customer",
    # Pronouns, determiners, interrogatives
    "he", "she", "it", "they", "we", "you", "i", "me", "him", "her", "us", "them",
    "my", "your", "his", "their", "our", "its", "who", "whom", "whose", "which", "what",
    "where", "when", "why", "how", "this", "that", "these", "those", "there", "here",
    "something", "anything", "nothing", "everything", "someone", "anyone", "somebody", "anybody", "nobody", "everybody", "whatever", "whoever", "whomever",
    "whenever", "wherever", "somehow", "somewhat", "somewhere", "anywhere", "nowhere",
}

# Standard English vocabulary (verbs, adjectives, prepositions) often capitalized by ASR
COMMON_ENGLISH_WORDS: Set[str] = {
    # Common English verbs (base, past, 3rd-person, participles)
    "alarm", "alarmed", "alarming", "alarms",
    "start", "started", "starting", "starts",
    "happen", "happened", "happening", "happens",
    "follow", "followed", "following", "follows",
    "mean", "meant", "meaning", "means",
    "know", "knew", "known", "knowing", "knows",
    "make", "made", "making", "makes",
    "take", "took", "taken", "taking", "takes",
    "come", "came", "coming", "comes",
    "go", "went", "gone", "going", "goes",
    "look", "looked", "looking", "looks",
    "seem", "seemed", "seeming", "seems",
    "feel", "felt", "feeling", "feels",
    "work", "worked", "working", "works",
    "call", "called", "calling", "calls",
    "give", "gave", "given", "giving", "gives",
    "get", "got", "gotten", "getting", "gets",
    "find", "found", "finding", "finds",
    "tell", "told", "telling", "tells",
    "ask", "asked", "asking", "asks",
    "try", "tried", "trying", "tries",
    "leave", "left", "leaving", "leaves",
    "need", "needed", "needing", "needs",
    "become", "became", "becoming", "becomes",
    "keep", "kept", "keeping", "keeps",
    "let", "letting", "lets",
    "begin", "began", "begun", "beginning", "begins",
    "help", "helped", "helping", "helps",
    "show", "showed", "shown", "showing", "shows",
    "hear", "heard", "hearing", "hears",
    "play", "played", "playing", "plays",
    "run", "ran", "running", "runs",
    "move", "moved", "moving", "moves",
    "like", "liked", "liking", "likes",
    "live", "lived", "living", "lives",
    "believe", "believed", "believing", "believes",
    "hold", "held", "holding", "holds",
    "bring", "brought", "bringing", "brings",
    "stop", "stopped", "stopping", "stops",
    "open", "opened", "opening", "opens",
    "close", "closed", "closing", "closes",
    "change", "changed", "changing", "changes",
    "create", "created", "creating", "creates",
    "add", "added", "adding", "adds",
    "use", "used", "using", "uses",
    "check", "checked", "checking", "checks",
    "review", "reviewed", "reviewing", "reviews",
    "decide", "decided", "deciding", "decides",
    "agree", "agreed", "agreeing", "agrees",
    "suggest", "suggested", "suggesting", "suggests",
    "talk", "talked", "talking", "talks",
    "speak", "spoke", "spoken", "speaking", "speaks",
    "say", "said", "saying", "says",
    "see", "saw", "seen", "seeing", "sees",
    "think", "thought", "thinking", "thinks",
    "want", "wanted", "wanting", "wants",
    "watch", "watched", "watching", "watches",
    "put", "putting", "puts",
    "set", "setting", "sets",
    "write", "wrote", "written", "writing", "writes",
    "read", "reading", "reads",
    "send", "sent", "sending", "sends",
    "receive", "received", "receiving", "receives",
    "build", "built", "building", "builds",
    "test", "tested", "testing", "tests",
    "deploy", "deployed", "deploying", "deploys",
    "push", "pushed", "pushing", "pushes",
    "pull", "pulled", "pulling", "pulls",
    "commit", "committed", "committing", "commits",
    "merge", "merged", "merging", "merges",
    "release", "released", "releasing", "releases",
    "fix", "fixed", "fixing", "fixes",
    "break", "broke", "broken", "breaking", "breaks",
    "allow", "allowed", "allowing", "allows",
    "provide", "provided", "providing", "provides",
    "include", "included", "including", "includes",
    "consider", "considered", "considering", "considers",
    "expect", "expected", "expecting", "expects",
    "remain", "remained", "remaining", "remains",
    "require", "required", "requiring", "requires",
    "report", "reported", "reporting", "reports",
    "explain", "explained", "explaining", "explains",
    "discuss", "discussed", "discussing", "discusses",
    "understand", "understood", "understanding", "understands",
    "remember", "remembered", "remembering", "remembers",
    "forget", "forgot", "forgotten", "forgetting", "forgets",
    "wonder", "wondered", "wondering", "wonders",
    "assume", "assumed", "assuming", "assumes",
    "suppose", "supposed", "supposing", "supposes",
    "hope", "hoped", "hoping", "hopes",
    "wish", "wished", "wishing", "wishes",
    "continue", "continued", "continuing", "continues",
    "meet", "met", "meeting", "meets",
    "drop", "dropped", "dropping", "drops",
    "pass", "passed", "passing", "passes",
    "fail", "failed", "failing", "fails",
    "win", "won", "winning", "wins",
    "lose", "lost", "losing", "loses",
    "occur", "occurred", "occurring", "occurs",
    "exist", "existed", "existing", "exists",
    "is", "am", "are", "was", "were", "be", "being", "been",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    # Common adjectives & adverbs
    "good", "better", "best", "bad", "worse", "worst",
    "new", "newest", "old", "oldest",
    "first", "second", "third", "last", "next", "previous",
    "early", "earlier", "earliest", "late", "later", "latest",
    "high", "higher", "highest", "low", "lower", "lowest",
    "big", "bigger", "biggest", "small", "smaller", "smallest",
    "great", "greater", "greatest", "long", "longer", "longest",
    "short", "shorter", "shortest", "large", "larger", "largest",
    "important", "critical", "major", "minor", "general", "specific",
    "main", "central", "public", "private", "internal", "external",
    "ready", "clear", "clarified", "hard", "easy", "simple",
    "complex", "real", "correct", "wrong", "different", "same", "similar",
    "other", "another", "such", "own", "only", "whole", "all", "both", "half",
    "many", "much", "more", "most", "several", "few", "fewer", "fewest",
    "well", "very", "quite", "too", "also", "even", "still", "just", "now", "then",
    "again", "already", "almost", "always", "never", "sometimes", "often", "usually",
    # Common prepositions & conjunctions
    "about", "above", "across", "after", "against", "along", "among", "around", "at",
    "before", "behind", "below", "beneath", "beside", "between", "beyond", "by",
    "down", "during", "except", "for", "from", "in", "inside", "into", "near",
    "of", "off", "on", "onto", "out", "outside", "over", "past", "since", "through",
    "throughout", "till", "to", "toward", "towards", "under", "underneath", "until",
    "up", "upon", "with", "within", "without",
    "and", "but", "or", "nor", "so", "yet", "because", "although", "though", "while",
    "if", "unless",
}

# Verbs that indicate communicative/relational interaction with a person
COMMUNICATIVE_VERBS: Set[str] = {
    "talk", "talked", "talking", "talks",
    "speak", "spoke", "spoken", "speaking", "speaks",
    "call", "called", "calling", "calls",
    "ask", "asked", "asking", "asks",
    "tell", "told", "telling", "tells",
    "mention", "mentioned", "mentioning", "mentions",
    "check", "checked", "checking", "checks",
    "reach", "reached", "reaching", "reaches",
    "sync", "synced", "syncing", "syncs",
    "align", "aligned", "aligning", "aligns",
    "meet", "met", "meeting", "meets",
    "discuss", "discussed", "discussing", "discusses",
    "hear", "heard", "hearing", "hears",
    "assign", "assigned", "assigning", "assigns",
    "delegate", "delegated", "delegating", "delegates",
    "consult", "consulted", "consulting", "consults",
}


def is_suppressed_or_common_word(token: str) -> bool:
    """Checks whether a token is a suppressed word, common English verb, adjective, or pronoun."""
    clean = token.strip()
    if not clean or len(clean) <= 1:
        return True
    lower = clean.lower()
    if lower in SUPPRESSED_WORDS or lower in COMMON_ENGLISH_WORDS:
        return True
    if not any(c.isalpha() for c in clean):
        return True
    return False


def is_valid_name_syntax(
    token: str,
    preceding_context: str,
    full_turn: str,
    is_in_directory: bool = False,
) -> bool:
    """Evaluates whether the syntactic context indicates a person rather than an ASR artifact.

    Prepositions like 'what' must NEVER trigger name extraction.
    Prepositions like 'to', 'on', 'for', 'about' require communicative verbs
    ('talked to', 'spoke with', 'asked', 'mentioned to') or an existing entry
    in the enterprise/graph directory.
    """
    if is_suppressed_or_common_word(token):
        return False

    clean_ctx = preceding_context.strip().lower()
    last_word = clean_ctx.split()[-1] if clean_ctx else ""

    # Prepositions like 'what' must NEVER trigger name extraction
    if last_word == "what" or clean_ctx.endswith("what"):
        # Check if the token itself is grounded and accompanied by speech verb
        turn_lower = full_turn.lower()
        has_speech_verb = bool(
            re.search(
                rf"\b{re.escape(token.lower())}\s+(?:was|said|says|agreed|suggested|mentioned|told|approved|signed|wanted|thinks|thought)\b",
                turn_lower,
            )
        )
        if is_in_directory and has_speech_verb:
            return True
        return False

    # If the token is already grounded in the enterprise directory
    if is_in_directory:
        return True

    # Prepositions like 'to', 'on', 'for', 'about', 'with', 'from', 'by'
    preposition_triggers = {"to", "on", "for", "about", "with", "from", "by", "at", "in"}
    if last_word in preposition_triggers:
        ctx_words = set(re.findall(r"\b[a-z]+\b", clean_ctx))
        if any(v in ctx_words for v in COMMUNICATIVE_VERBS):
            return True
        return False

    # Check for speech or relational verbs directly following the token
    turn_lower = full_turn.lower()
    tok_pattern = rf"\b{re.escape(token.lower())}\s+(?:was|said|says|agreed|suggested|mentioned|told|approved|signed|wanted|thinks|thought|stated|confirmed|noted|recommended)\b"
    if re.search(tok_pattern, turn_lower):
        return True

    # Check for possessive reference with meaningful noun
    possessive_pattern = rf"\b{re.escape(token.lower())}'s\s+(?:feedback|team|robot|approach|rules|threshold|call|input|thoughts|review|code)\b"
    if re.search(possessive_pattern, turn_lower):
        return True

    return False


def should_surface_unconfirmed_candidate(
    token: str,
    turn_frequency: int,
    has_relational_verb: bool,
) -> bool:
    """Evaluates whether an ungrounded OOV candidate token has sufficient evidence to surface.

    Rejects single-occurrence isolated OOV tokens that have no graph match and no relational reinforcement.
    Under C-12 / C-27, referenced persons must have positive evidence or explicit multi-mention
    corroboration before elevation.
    """
    if is_suppressed_or_common_word(token):
        return False
    if turn_frequency <= 1 and not has_relational_verb:
        return False
    return True
