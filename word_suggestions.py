"""Word completion for the on-screen keyboard.

As the user types, the keyboard shows a few whole-word suggestions; one blink
on a suggestion finishes the word, saving several key presses.

Suggestions come from a built-in list of common English words ordered roughly
by how often they are used, followed by everyday care and communication words.
Nothing the user types is learned or saved, so text such as passwords typed on
the keyboard can never reappear as a suggestion.

Pure Python, no Qt, so it can be unit tested directly.
"""

from __future__ import annotations

import re
from typing import List

MAX_SUGGESTIONS = 4

_COMMON = """
the be to of and a in that have i it for not on with he as you do at this but his by
from they we say her she or an will my one all would there their what so up out if
about who get which go me when make can like time no just him know take people into
year your good some could them see other than then now look only come its over think
also back after use two how our work first well way even new want because any these
give day most us is was are been has had were said did made went got told found
thought called asked felt seemed left kept let began took came saw knew gave
i'm don't it's can't i'll that's didn't won't i've isn't doesn't you're we're
let's there's what's wasn't couldn't wouldn't shouldn't i'd they're aren't
yes please thank thanks sorry hello hi okay ok bye goodbye welcome
need help feel feeling want wants needed helping
very really much more many lot little less too enough again still already always
never sometimes often soon later today tonight tomorrow yesterday morning afternoon
evening night week month weekend now here there where why how what when who which
right left up down open close start stop wait turn move sit stand lie sleep wake rest
eat drink water food breakfast lunch dinner tea coffee milk juice soup bread fruit
hot cold warm cool hungry thirsty tired sick well better worse fine happy sad
angry scared worried bored comfortable uncomfortable pain hurt hurts itch itchy
head neck back shoulder arm hand leg foot feet chest stomach eyes mouth throat
breathe breathing breath cough phlegm saliva swallow swallowing choking
medicine medication pill pills tablet dose doctor nurse caregiver hospital clinic
appointment therapy physiotherapy therapist emergency ambulance call phone message
bathroom toilet shower bath wash clean change clothes blanket pillow bed chair
wheelchair position reposition adjust lift turn ventilator suction feeding tube
glasses light lights dark loud quiet noise music television tv radio volume
family mother father mom dad son daughter wife husband brother sister friend
friends children child baby grandchild home house room kitchen window door
love miss proud grateful laugh smile cry hug kiss visit visiting together
read write type typing keyboard computer screen email internet search video
watch listen play game book news weather outside garden walk car drive
money bank pay bill shopping order buy send receive
question answer understand remember forget mean talk speak say tell ask
something nothing everything anything someone everyone anyone nobody
again maybe probably definitely sure think believe hope wish guess
because about before after during while until since through without within
between under above behind near far next last early late fast slow quickly slowly
important problem idea reason example information question number part place
point case group system program company government world country city state
school student family life hand eye woman man child person thing name word
water story fact money month lot right study book job business issue side kind
head house service friend father power hour game line end member law car city
community president team minute idea kid body information back parent face others
level office door health person art war history party result change morning reason
research girl guy moment air teacher force education foot boy age policy process
music market sense nation plan college interest death experience effect use class
control care field development role effort rate heart drug show leader light voice
wife police mind price report decision son view relationship town road arm
difference value building action model season society tax director position player
record paper space ground form event official matter center couple site project
activity star table need court oil situation cost industry figure street image phone
data picture practice piece land product doctor wall patient worker news test movie
north love support technology step baby computer type attention film tree source
organization hair window evidence population site truth
would should could might must shall may can will
going doing being having getting making taking coming looking saying working
trying using finding giving telling calling asking feeling leaving putting meaning
keeping letting beginning seeming helping showing hearing playing running moving
living believing bringing happening writing providing sitting standing losing paying
meeting including continuing setting learning changing leading understanding
watching following stopping creating speaking reading allowing adding spending
growing opening walking winning offering remembering loving considering appearing
buying waiting serving dying sending expecting building staying falling cutting
reaching killing remaining suggesting raising passing selling requiring reporting
deciding pulling
able bad best better big black blue certain clear common different early easy
economic entire free full general great green hard high human large late
local long low major military national new old only other personal political
poor possible public real recent red short simple single small social special
strong sure true white whole young beautiful wonderful terrible awful nice lovely
difficult available similar likely necessary several various
"""

_WORD_RE = re.compile(r"[A-Za-z']+$")


def _build_words(text: str) -> List[str]:
    seen = set()
    words = []
    for word in text.split():
        word = word.strip().lower()
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    return words


WORDS: List[str] = _build_words(_COMMON)

_PRONOUN_I = {"i", "i'm", "i'll", "i've", "i'd"}


def display_form(word: str) -> str:
    """How a dictionary word should appear, e.g. 'i' -> 'I'."""
    if word in _PRONOUN_I:
        return "I" + word[1:]
    return word


def current_word(text_before_cursor: str) -> str:
    """The partial word immediately before the cursor ('' after a space)."""
    match = _WORD_RE.search(text_before_cursor or "")
    return match.group(0) if match else ""


def suggest(prefix: str, limit: int = MAX_SUGGESTIONS) -> List[str]:
    """Up to `limit` whole words that start with `prefix`, most common first."""
    if not prefix:
        return []
    lowered = prefix.lower()
    matches = []
    for word in WORDS:
        if word.startswith(lowered) and word != lowered:
            matches.append(display_form(word))
            if len(matches) >= limit:
                break
    return matches


def completion_suffix(prefix: str, word: str) -> str:
    """Characters still to type to turn `prefix` into `word`.

    Keeps the user's capitalization: an all-caps prefix (two or more letters)
    continues in caps; otherwise the rest of the word is typed as listed.
    """
    rest = word[len(prefix):]
    if len(prefix) > 1 and prefix.isupper():
        return rest.upper()
    return rest
