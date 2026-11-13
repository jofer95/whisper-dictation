"""Offline transcript clean-up — the rule-based half of Wispr Flow's "Auto
Cleanup", "Backtrack", dictionary and snippets, with no cloud and no LLM.

Everything here is pure string manipulation (no AppKit), so it is unit-tested
in tests/test_textproc.py. The pipeline, in order:

  1. voice commands   "scratch that" / "borra eso" drop the previous sentence;
                      "new line" / "nueva línea", "new paragraph" / "punto y aparte".
  2. backtrack        "at 2, actually 3" -> "at 3"; "a las 2, no, a las 3" -> "a las 3".
  3. fillers          um / uh / eh / mmm … (plus the comma Whisper puts after them).
  4. stutters         "the the" -> "the" (function words only; "no no" is kept).
  5. dictionary       corrections ("wisper flow" -> "Wispr Flow") and canonical
                      casing of your words ("github" -> "GitHub").
  6. snippets         a spoken trigger is replaced by its saved text.
  7. context          spacing / capitalisation / trailing period matched to the
                      text around the cursor, and casual punctuation in chat apps.

Conservative by design: when a rule could plausibly change meaning, it doesn't
fire. Wispr does the fuzzy cases with a cloud LLM; we'd rather leave text as
spoken than rewrite it wrong.
"""

import re

# ---------------------------------------------------------------- helpers ----

_SENT_END = ".!?…"
_WORD_RE = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)


def _cap_first(text: str) -> str:
    """Capitalise the first word if it is entirely lowercase (keeps iPhone, eBay)."""
    m = _WORD_RE.search(text)
    if not m or m.start() > 3:  # allow leading ¿ ¡ ( "
        return text
    word = m.group(0)
    if word != word.lower():
        return text
    return text[:m.start()] + word[0].upper() + word[1:] + text[m.end():]


def _tidy(text: str) -> str:
    """Fix the spacing/punctuation debris left after removing words."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.;:!?…])", r"\1", text)          # "word ," -> "word,"
    text = re.sub(r",\s*([.!?…])", r"\1", text)           # ",." -> "."
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)    # ",," -> ","
    text = re.sub(r"(^|\n)[ \t]*[,;:]+[ \t]*", r"\1", text)  # leading ", "
    text = re.sub(r"([¿¡])\s*,\s*", r"\1", text)           # "¿, qué" -> "¿qué"
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.strip(" \t")


def _recapitalise(text: str) -> str:
    """Capitalise the start of the text and of every sentence / line."""
    out = _cap_first(text)
    def fix(m):
        return m.group(1) + _cap_first(m.group(2))
    return re.sub(r"([.!?…]\s+|\n+)(\S.*?)(?=$|[.!?…]\s|\n)", fix, out, flags=re.S)


# ----------------------------------------------------------- 1. commands ----

_SCRATCH = re.compile(
    r"[,.;:]?\s*\b(?:scratch that|delete that|borra eso|bórralo|borralo|"
    r"borra lo anterior|olvida eso)\b[.,!;:]?", re.I)
_NEWPARA = re.compile(
    r"[,;:]?\s*\b(?:new paragraph|nuevo p[aá]rrafo|punto y aparte)\b[.,;:]?\s*", re.I)
_NEWLINE = re.compile(
    r"[,;:]?\s*\b(?:new line|newline|nueva l[ií]nea|salto de l[ií]nea)\b[.,;:]?\s*", re.I)


def apply_voice_commands(text: str) -> str:
    # "scratch that": drop the sentence (or clause) right before the command.
    while True:
        m = _SCRATCH.search(text)
        if not m:
            break
        before = text[:m.start()].rstrip().rstrip(_SENT_END + ",;:").rstrip()
        cut = max(before.rfind(ch) for ch in _SENT_END + "\n")
        kept = before[:cut + 1] if cut >= 0 else ""
        after = text[m.end():].lstrip()
        text = (kept + (" " if kept and after and not kept.endswith("\n") else "") + after)

    def para(m):
        # "punto y aparte" literally means "full stop, new paragraph".
        return ".\n\n" if "punto" in m.group(0).lower() else "\n\n"
    text = _NEWPARA.sub(para, text)
    text = _NEWLINE.sub("\n", text)
    text = re.sub(r"([.!?…])\.\n", r"\1\n", text)   # avoid "?.\n\n"
    text = re.sub(r"^\.\n", "\n", text)
    return text


# ---------------------------------------------------------- 2. backtrack ----

_NUM_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce"
)
_NUM = (r"(?:\d+(?:[:.]\d+)?|" + _NUM_WORDS + r")"
        r"(?:\s?(?:am|pm|a\.m\.|p\.m\.|hrs|horas))?")
_CORRECTION = (r"actually|i mean|no wait|wait no|sorry|no|perd[oó]n|digo|"
               r"mejor dicho|bueno no|no mejor|o sea")
# "<prep> 2, no, <prep> 3" (Spanish repeats the preposition) or "2 actually 3".
_BACKTRACK = re.compile(
    r"\b(?:(?P<prep>a las|a la|at|by|on|el|la|los|las)\s+)?" + _NUM +
    r"\s*,?\s*(?:" + _CORRECTION + r")\s*,?\s*"
    r"(?:(?P=prep)\s+)?(?P<new>" + _NUM + r")\b", re.I)


def apply_backtrack(text: str) -> str:
    def repl(m):
        prep = m.group("prep")
        return (prep + " " if prep else "") + m.group("new")
    return _BACKTRACK.sub(repl, text)


# ------------------------------------------------------------ 3. fillers ----

_FILLERS = {
    "en": r"u+m+|u+h+m*|e+r+m+|h+m+|m+h+m+|m{2,}",
    "es": r"e+h+m*|e+m{2,}|m{2,}|u+m+|u+h+|e+e+h*",
}
_FILLERS["*"] = _FILLERS["en"] + "|" + _FILLERS["es"]


def remove_fillers(text: str, language: str | None = None) -> str:
    if language is not None and language not in _FILLERS:
        return text  # e.g. Portuguese "um" is a real word — only touch es/en
    alt = _FILLERS[language or "*"]
    pat = re.compile(r"(?<![\w'’-])(?:" + alt + r")(?![\w'’-])[,.…]*", re.I)

    def drop(m):
        word = m.group(0).rstrip(",.…")
        return m.group(0) if len(word) > 1 and word.isupper() else ""  # ERM, HMM = acronyms
    return pat.sub(drop, text)


# ----------------------------------------------------------- 4. stutters ----

_STUTTER_WORDS = (
    "i|a|the|to|and|of|in|it|is|we|you|my|el|la|los|las|de|que|en|y|un|una|"
    "lo|le|se|me|te|es|con|por|para|mi|su"
)
_STUTTER = re.compile(r"\b(" + _STUTTER_WORDS + r")(?:\s+\1\b)+", re.I)


def remove_stutters(text: str) -> str:
    return _STUTTER.sub(lambda m: m.group(1), text)


# --------------------------------------------- 5/6. dictionary + snippets ----

def _phrase_pattern(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in phrase.split()]
    body = r"[\s,.-]+".join(words)
    return re.compile(r"(?<![\w@])" + body + r"(?![\w@])", re.I)


def apply_dictionary(text: str, words=(), corrections=None) -> str:
    """Corrections first (longest match wins), then canonical casing of words."""
    corrections = corrections or {}
    for wrong in sorted(corrections, key=len, reverse=True):
        right = corrections[wrong]
        if wrong.strip():
            text = _phrase_pattern(wrong).sub(lambda _m, r=right: r, text)
    for word in sorted(words, key=len, reverse=True):
        if word.strip():
            text = _phrase_pattern(word).sub(lambda _m, w=word: w, text)
    return text


def apply_snippets(text: str, snippets=None) -> str:
    """Replace spoken triggers with their saved text. If the whole dictation is
    just the trigger ("My email."), return the expansion alone, unpunctuated."""
    snippets = snippets or {}
    if not snippets:
        return text
    bare = text.strip().strip(_SENT_END + ",;:¿¡").strip().lower()
    for trigger, expansion in snippets.items():
        if bare == trigger.strip().lower():
            return expansion
    for trigger in sorted(snippets, key=len, reverse=True):
        if trigger.strip():
            text = _phrase_pattern(trigger).sub(
                lambda _m, e=snippets[trigger]: e, text)
    return text


# ------------------------------------------------------------ 7. context ----

# Words we may safely lower-case when continuing a sentence mid-way
# ("…and then" not "…And then"). Proper nouns are never in this list.
_COMMON = set("""
a about after again all also am an and any are as at be because been before
but by can could did do does for from had has have he her here his how i'd if
in into is it it's its just let's like maybe me more my no not now of ok okay on
once or our please she should so some still than thanks that the their them then
there these they this those to too us very was we well were what when where
which while who why will with would yes yet you your really actually
basically probably definitely only even still almost always never sometimes
good great nice sure right new big small first last next other same
a al algo así aunque bien bueno cada como con cuando de del donde el ella ellos
en entonces era es esa ese eso esta este esto está fue gracias hay la las le les
lo los luego mas más me mi muy nada ni no nos o otra otro para pero por porque
pues que qué se si sin sobre solo son su también te tu un una unos unas y ya
""".split())

CHAT_BUNDLES = {
    "com.tinyspeck.slackmacgap", "com.apple.MobileSMS", "net.whatsapp.WhatsApp",
    "desktop.WhatsApp", "com.microsoft.teams", "com.microsoft.teams2",
    "com.hnc.Discord", "ru.keepcoder.Telegram", "org.telegram.desktop",
    "com.facebook.archon", "com.facebook.Messenger", "org.whispersystems.signal-desktop",
}


def apply_context(text: str, before: str | None = None, after: str | None = None,
                  casual: bool = False) -> str:
    """Match the dictation to the text around the cursor.

    `before` / `after` are a few characters either side of the insertion point
    (None when unknown — e.g. the app doesn't expose Accessibility text).
    """
    if not text:
        return text

    if before is not None:
        prev = before.rstrip(" \t")
        last = prev[-1:] if prev else ""
        if last == "" or last in _SENT_END or last == "\n":
            text = _cap_first(text)
        elif last.isalnum() or last in ",;:—-–":
            m = _WORD_RE.match(text)
            if m:
                word = m.group(0)
                if (word[:1].isupper() and word[1:] == word[1:].lower()
                        and word.lower() in _COMMON and word not in ("I", "I'd")):
                    text = word.lower() + text[m.end():]
        # Leading space when glued to a previous word/punctuation.
        if before and not before[-1].isspace() and before[-1] not in "([{\"'“‘¿¡/@#-\n":
            if text[:1].isalnum() or text[:1] in "¿¡(\"“":
                text = " " + text

    if after is not None:
        nxt = after.lstrip(" \t")[:1]
        if nxt and nxt not in "\n" and not nxt.isupper():
            # Inserting mid-sentence: no full stop, and a space before the rest.
            if text.endswith(".") and not text.endswith(".."):
                text = text[:-1]
            if after[:1].isalnum():
                text = text + " "

    if casual:
        # Chat apps: a single sentence goes out without the final full stop.
        stripped = text.rstrip()
        trail = text[len(stripped):]
        if (stripped.endswith(".") and not stripped.endswith("..")
                and not re.search(r"[.!?…]\s", stripped[:-1])):
            text = stripped[:-1] + trail
    return text


# --------------------------------------------------------------- pipeline ----

def process(text: str, *, language: str | None = None, remove_filler_words=True,
            voice_commands=True, words=(), corrections=None, snippets=None) -> str:
    """Steps 1–6 (everything that doesn't need to know the cursor context)."""
    if not text:
        return text
    original = text
    if voice_commands:
        text = apply_voice_commands(text)
        text = apply_backtrack(text)
    if remove_filler_words:
        text = remove_fillers(text, language)
        text = remove_stutters(text)
    text = apply_dictionary(text, words, corrections)
    text = _tidy(text)
    if text != original:
        text = _recapitalise(text)
    text = apply_snippets(text, snippets)
    return text
