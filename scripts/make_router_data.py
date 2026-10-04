"""Regenerate data/router/*.txt - training examples for the LOCAL_CHAT / WEB_SEARCH / ONLINE_LLM classifier.

Hand-written seed sentences + slot templates, sampled with a fixed seed so the output is
reproducible. Edit the lists below (or add lines to the .txt files by hand) and re-run:

  python scripts/make_router_data.py && python scripts/train_classifier.py

The held-out evaluation set (data/router_eval.tsv) is written by hand separately and must
not be generated from these templates, otherwise its accuracy number means nothing.
"""

from __future__ import annotations

import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "router"
rng = random.Random(42)
PER_CLASS = 650


def fill(templates: list[str], slots: dict[str, list[str]], n: int) -> list[str]:
    out = set()
    tries = 0
    while len(out) < n and tries < n * 50:
        tries += 1
        t = rng.choice(templates)
        for k, vals in slots.items():
            while "{" + k + "}" in t:
                t = t.replace("{" + k + "}", rng.choice(vals), 1)
        out.add(t)
    return list(out)


def noisy(s: str) -> str:
    """Real typing: lower case, missing '?', the odd typo."""
    r = rng.random()
    if r < 0.25:
        s = s.lower()
    if r > 0.7:
        s = s.rstrip("?.!")
    if rng.random() < 0.06 and len(s) > 12:
        i = rng.randrange(3, len(s) - 3)
        s = s[:i] + s[i + 1:]
    return s


# ------------------------------------------------------------------ LOCAL_CHAT
# Things a 0.5B model handles fine: small talk, timeless facts, short definitions,
# simple how-tos on the PC, tiny stories, feelings, kids' questions.
LOCAL_SEEDS = """hi
hello
hey there
good morning
good night
how are you
how are you doing today
what's your name
who are you
what can you talk about
thank you
thanks a lot
you're awesome
tell me a joke
tell me another joke
make me laugh
tell me a riddle
tell me a fun fact
tell me something interesting
i'm bored
i feel sad
i'm feeling lonely
i'm tired
cheer me up
say something nice
do you like music
what's your favorite color
are you a robot
can you be my friend
tell me a short story
tell me a bedtime story
tell me a story about a dragon
sing me a song
what is a noun
what is a verb
what is an adjective
what does photosynthesis mean
what is gravity
why is the sky blue
why do cats purr
why do leaves change color
how many legs does a spider have
how many days are in a year
how many continents are there
how many planets are in the solar system
what is the biggest animal
what is the tallest mountain
what is the capital of France
what is the capital of Japan
what is the largest ocean
what language do they speak in Brazil
who wrote Romeo and Juliet
who painted the Mona Lisa
what is the boiling point of water
how many centimeters are in an inch
how many grams in a kilogram
how many minutes in an hour
how do you spell necessary
how do you spell beautiful
what is the opposite of happy
give me a word that rhymes with cat
what does RAM mean
what is a CPU
what is a browser
what is wifi
what is an email
what is the cloud
what is a password
what is a gigabyte
how do I take a screenshot
how do I copy and paste
how do I zoom in
how do I make the text bigger
how do I change my wallpaper
how do I restart my computer
how do I turn up the volume
how do I connect to wifi
how do I make a new folder
how do I rename a file
how do I close a program
what keyboard shortcut undoes something
how do I print a page
how do I type a capital letter
what should I eat for dinner
give me a quick tip for sleeping better
how can I relax
how much water should I drink
what is a good name for a puppy
suggest a name for my cat
what games can kids play outside
what is two plus two
what color do you get mixing blue and yellow
what sound does a cow make
can you count to ten
what day comes after monday
what is the meaning of the word brave
give me a synonym for big
what does the word curious mean
explain what a rainbow is
what is a dinosaur
are dolphins mammals
do fish sleep
what do bees make
good job
ok cool
nice
that's funny
i love you
goodbye
see you later
who made you
what do you do
are you smart
can you help me
what is a computer virus
what is an app
what is the internet""".splitlines()

LOCAL_T = [
    "what is a {thing}", "what does {thing} mean", "explain what a {thing} is", "tell me about {animal}s",
    "how many {unit_a} are in a {unit_b}", "what is the capital of {country}", "tell me a joke about {topic}",
    "tell me a short story about {topic}", "tell me a fun fact about {animal}s", "why do {animal}s {verb}",
    "how do I {pc_task}", "how can I {pc_task}", "can you tell me how to {pc_task}", "what is the plural of {word}",
    "how do you spell {word}", "what rhymes with {word}", "give me a synonym for {adj}", "what is the opposite of {adj}",
    "i feel {feeling}", "i'm feeling {feeling} today", "what is your favorite {fav}", "do you like {fav}s",
    "{greet}", "{greet}, how are you", "what sound does a {animal} make", "is a {animal} a mammal",
    "what color is a {animal}", "where do {animal}s live", "what do {animal}s eat",
]
LOCAL_SLOTS = {
    "thing": ["noun", "verb", "planet", "volcano", "keyboard", "mouse", "printer", "monitor", "file", "folder",
              "desktop", "router", "webcam", "battery", "hard drive", "USB stick", "pixel", "fraction", "triangle",
              "rainbow", "cloud", "magnet", "habit", "poem", "recipe", "museum", "library", "calendar", "compass"],
    "animal": ["cat", "dog", "elephant", "penguin", "dolphin", "owl", "bee", "lion", "rabbit", "horse", "frog",
               "shark", "parrot", "turtle", "giraffe", "butterfly", "cow", "duck", "bat", "whale"],
    "unit_a": ["seconds", "minutes", "centimeters", "inches", "grams", "days", "weeks", "millimeters", "ounces"],
    "unit_b": ["minute", "hour", "meter", "foot", "kilogram", "week", "year", "centimeter", "pound"],
    "country": ["Italy", "Spain", "Canada", "Egypt", "India", "Kenya", "Peru", "Norway", "Australia", "Germany",
                "Mexico", "Thailand", "Bangladesh", "Ghana", "Chile"],
    "topic": ["a cat", "a robot", "pirates", "the moon", "a brave mouse", "computers", "school", "a dragon",
              "dinosaurs", "the ocean", "a lost dog", "friendship", "space", "a magic tree"],
    "verb": ["sleep so much", "purr", "bark", "fly south", "hibernate", "have tails", "swim", "sing"],
    "pc_task": ["take a screenshot", "copy text", "paste something", "make the font bigger", "lock my screen",
                "open the calculator", "change the brightness", "mute the sound", "empty the recycle bin",
                "find the start menu", "switch between windows", "use the on-screen keyboard", "minimize a window",
                "save a document", "plug in headphones", "turn on bluetooth", "use the magnifier", "right click"],
    "word": ["cat", "house", "child", "mouse", "tomato", "friend", "knife", "receive", "separate", "tomorrow"],
    "adj": ["big", "happy", "fast", "cold", "brave", "small", "angry", "quiet", "bright", "old"],
    "feeling": ["sad", "happy", "bored", "tired", "lonely", "nervous", "excited", "sleepy", "grumpy", "great"],
    "fav": ["color", "food", "animal", "song", "movie", "game", "book", "season"],
    "greet": ["hi", "hello", "hey", "good morning", "good evening", "hiya", "yo", "howdy"],
}

# ------------------------------------------------------------------ WEB_SEARCH
# Needs fresh or local information the model can't know: news, prices, weather,
# scores, releases, schedules, opening hours, people's current status.
WEB_SEEDS = """what's the weather today
will it rain tomorrow
weather this weekend
what's the latest news
any news today
top headlines right now
who won the game last night
what was the score of the match
football scores today
cricket score live
when is the next world cup
nba results yesterday
bitcoin price
price of gold today
how much is an iphone right now
dollar to euro exchange rate today
tesla stock price
best laptops this year
latest version of windows
latest version of python
when does the new season come out
release date of the next gta
new movies in theaters this week
movie showtimes near me
pharmacy open now near me
restaurants near me
is youtube down right now
traffic on the highway now
flight status
train times to the city today
election results
who is the current prime minister of the uk
who is the president of the united states now
how old is taylor swift
what time does the bank close today
is the post office open today
what's trending on twitter
gas prices near me
latest iphone release
when is black friday this year
public holidays this year
covid cases today
earthquake news
what happened today in the world
latest apple announcement
cheapest flights to london next month
hotel prices in paris this weekend
lyrics of the latest song by adele
new album release this week
sports schedule this weekend
champions league fixtures
premier league table
air quality today
sunset time today
pollen count today
current interest rates
mortgage rates today
new phone launches this month
latest nvidia driver version
latest chrome version
what's on tv tonight
concerts near me this month
is there a storm coming
temperature right now
latest ai news
news about spacex launch
did the launch happen today
when is the next solar eclipse
who won the oscars this year
grammy winners this year
latest marvel movie
box office this weekend""".splitlines()

WEB_T = [
    "what's the weather in {city} {when}", "weather {city} {when}", "will it rain in {city} {when}",
    "{when} forecast for {city}", "latest news about {news}", "{news} news today", "what happened with {news}",
    "price of {item} {now}", "how much does {item} cost {now}", "{item} price {now}", "{stock} stock price",
    "is {stock} stock up today", "who won the {sport} {when2}", "{sport} score {when2}", "{team} score {when2}",
    "when does {team} play next", "{team} schedule this week", "release date of {product}",
    "when is {product} coming out", "latest version of {software}", "is {software} down right now",
    "{place} near me", "{place} open now", "what time does the {place} close today", "{event} {year}",
    "when is {event} {year}", "exchange rate {cur} to {cur2} today", "how old is {celeb} now",
    "what is {celeb} doing now", "latest {celeb} news", "flights from {city} to {city2} {when3}",
    "hotels in {city} {when3}", "traffic in {city} right now", "events in {city} this weekend",
]
WEB_SLOTS = {
    "city": ["London", "Dhaka", "New York", "Paris", "Tokyo", "Berlin", "Delhi", "Sydney", "Toronto", "Lagos",
             "Chicago", "Dubai", "Madrid", "Seoul", "Cairo"],
    "city2": ["Rome", "Bangkok", "Istanbul", "Boston", "Singapore", "Lisbon", "Nairobi", "Mumbai"],
    "when": ["today", "tomorrow", "tonight", "this weekend", "this week", "right now", "on friday", "next week"],
    "when2": ["last night", "today", "yesterday", "this week", "on sunday", "in the final", "this season"],
    "when3": ["next month", "this weekend", "in december", "tomorrow", "next week"],
    "news": ["the election", "the stock market", "climate change", "the war", "spacex", "openai", "apple",
             "the economy", "inflation", "the olympics", "the hurricane", "elon musk", "the world cup"],
    "item": ["bitcoin", "gold", "silver", "ethereum", "an iphone 16", "a ps5", "rice", "petrol", "a tesla model 3",
             "a macbook air", "an rtx 4060", "eggs", "a flight to dubai", "the nintendo switch 2"],
    "now": ["today", "right now", "now", "this week", "in 2026", "currently"],
    "stock": ["apple", "tesla", "nvidia", "microsoft", "amazon", "google", "meta", "netflix", "amd", "intel"],
    "sport": ["match", "game", "race", "final", "derby", "cup final", "grand prix", "super bowl", "world series"],
    "team": ["real madrid", "barcelona", "arsenal", "the lakers", "manchester united", "india", "bangladesh",
             "the yankees", "liverpool", "juventus"],
    "product": ["gta 6", "the next iphone", "windows 12", "the new zelda", "stranger things season 6",
                "the next avengers movie", "the pixel 10", "the switch 2", "the next harry potter series"],
    "software": ["chrome", "firefox", "python", "node", "windows", "android", "ios", "vlc", "steam", "discord",
                 "whatsapp", "zoom", "gmail", "netflix", "instagram"],
    "place": ["pharmacy", "gas station", "pizza place", "atm", "hospital", "supermarket", "coffee shop",
              "post office", "bank", "library", "gym", "dentist"],
    "event": ["black friday", "eid", "diwali", "christmas sales", "the next full moon", "the champions league final",
              "the world cup", "the next apple event", "tax day", "the olympics"],
    "year": ["this year", "2026", "2027", "next year"],
    "cur": ["usd", "euro", "pound", "taka", "yen", "rupee"],
    "cur2": ["eur", "usd", "bdt", "inr", "gbp", "jpy"],
    "celeb": ["taylor swift", "messi", "ronaldo", "elon musk", "beyonce", "shakira", "the pope", "drake",
              "keanu reeves", "obama"],
}

# ------------------------------------------------------------------ ONLINE_LLM
# Real work: writing, code, debugging, deep comparisons, summaries and translations
# of pasted text, explanations of "why", plans, harder math.
ONLINE_SEEDS = """write a cover letter for a nursing job
write an essay about climate change
write a 500 word blog post about healthy eating
write a professional email asking my boss for a raise
draft a complaint letter to my landlord about the heating
write a wedding speech for my brother
write a poem about my grandmother for her funeral
write a business plan for a small bakery
help me write my resume
rewrite this paragraph to sound more professional
proofread my essay and fix the grammar
summarize this article for me
summarize the following text in three bullet points
translate this letter into spanish
translate this paragraph to french
fix my python code
debug this javascript function
why does my code throw a null pointer exception
refactor this function to be faster
write a python script that renames all files in a folder
write a sql query to find duplicate rows
write a regex that matches email addresses
explain why the roman empire fell
explain why inflation happens in detail
compare python and javascript for a beginner
compare the iphone and pixel cameras in detail
what are the pros and cons of nuclear energy, explain thoroughly
plan a 7 day trip to japan with a budget
create a weekly workout plan for weight loss
make a meal plan for a diabetic for one week
solve this equation 3x + 7 = 22 and show the steps
find the derivative of x^3 sin x
integrate x e^x dx
prove that the square root of 2 is irrational
explain quantum entanglement in depth
help me understand my lease agreement
analyze this data and find trends
write a short story of 2000 words about time travel
create a lesson plan for teaching fractions
write unit tests for this function
convert this java code to python
explain this error message to me
how do I implement a binary search tree in c++
design a database schema for an online shop
review my code for security problems
write a marketing plan for my youtube channel
help me prepare for a job interview as a data analyst
give me a detailed study plan for the ielts exam
outline a research paper on renewable energy
write a song with verses and a chorus about summer love""".splitlines()

ONLINE_CODE = [
    "Traceback (most recent call last):\\n  File \"app.py\", line 12, in <module>\\n    main()\\nTypeError: 'NoneType' object is not subscriptable",
    "why does this fail?\\n```python\\ndef add(a, b):\\n    return a + b\\nprint(add('1', 2))\\n```",
    "fix this:\\nfunction sum(arr) {\\n  let t = 0;\\n  for (i = 0; i <= arr.length; i++) t += arr[i];\\n  return t;\\n}",
    "Error: Cannot find module 'express'\\n    at Function.Module._resolveFilename (node:internal/modules/cjs/loader:1039:15)",
    "my java program says Exception in thread \"main\" java.lang.NullPointerException at Main.main(Main.java:5) what do I do",
    "SELECT name, COUNT(*) FROM users GROUP BY name HAVING COUNT(*) > 1; why is this slow on a big table",
    "segfault in my c program when I free a pointer twice, explain",
    "pip install fails with error: Microsoft Visual C++ 14.0 or greater is required",
    "class Stack:\\n    def __init__(self):\\n        self.items = []\\nadd push and pop methods and tests",
    "KeyError: 'id' in my flask app when reading request.json, how do I fix it",
    "npm ERR! code ERESOLVE unable to resolve dependency tree, how to fix",
    "IndexError: list index out of range on line 8, here is my loop for i in range(len(a)+1): print(a[i])",
]
ONLINE_T = [
    "write a {doc} about {subject}", "write me a {doc} for {who}", "draft a {doc} to {who} about {subject}",
    "help me write a {doc} about {subject}", "can you write a {length} {doc} on {subject}",
    "summarize this {text}: {pasted}", "translate this {text} into {lang}: {pasted}",
    "rewrite this {text} so it sounds {tone}: {pasted}", "proofread this {text}: {pasted}",
    "explain why {deep} in detail", "explain why {deep}", "can you explain in depth how {deep2}",
    "compare {a} and {b} in detail", "compare {a} vs {b} for {who2}, with pros and cons",
    "which is better for {who2}, {a} or {b}? give a detailed comparison",
    "write a {plang} function that {task}", "write a {plang} script to {task}", "fix my {plang} code that {task}",
    "debug my {plang} program, it crashes when it {task}", "refactor this {plang} code that {task}",
    "how do I {task} in {plang}, show the code", "plan a {days} day trip to {country} for a family",
    "create a {days} day {plan} for {who2}", "make a detailed {plan} for {who2}",
    "{mathq}", "solve step by step: {mathq}", "analyze {subject} and give me a report",
]
ONLINE_SLOTS = {
    "doc": ["cover letter", "essay", "speech", "blog post", "resignation letter", "report", "short story",
            "poem with four verses", "product description", "linkedin post", "proposal", "thank you letter",
            "complaint email", "eulogy", "newsletter"],
    "subject": ["climate change", "social media and teenagers", "my first job", "artificial intelligence",
                "the history of the internet", "why reading matters", "our quarterly sales", "remote work",
                "mental health at work", "the benefits of recycling", "my startup idea", "renewable energy"],
    "who": ["my boss", "my landlord", "a hiring manager", "my team", "the school principal", "my bank",
            "a customer", "my professor", "the city council", "my wife"],
    "length": ["1000 word", "two page", "detailed", "500 word", "long", "formal"],
    "text": ["paragraph", "article", "email", "text", "letter", "passage", "message"],
    "pasted": ["The quarterly results show a 12 percent increase in revenue driven mainly by online sales, while "
               "costs grew faster than expected due to shipping delays and rising wages across all regions.",
               "Dear Sir, I am writing to inform you that the package I ordered three weeks ago has still not "
               "arrived and nobody answers the phone at your customer service number.",
               "Climate models predict that average temperatures will continue to rise over the coming decades, "
               "with significant consequences for agriculture, coastal cities and biodiversity.",
               "We regret to inform you that the meeting scheduled for Monday has been postponed until further "
               "notice because several key participants are unavailable."],
    "lang": ["spanish", "french", "german", "bangla", "hindi", "arabic", "japanese", "portuguese", "chinese"],
    "tone": ["more professional", "friendlier", "more polite", "shorter and clearer", "more formal", "persuasive"],
    "deep": ["the roman empire collapsed", "the stock market crashed in 1929", "inflation hurts savers",
             "airplanes can fly", "vaccines create immunity", "the cold war started", "prices rise when money is printed",
             "my startup idea might fail", "neural networks need so much data", "the french revolution happened"],
    "deep2": ["blockchain works", "a compiler works", "the immune system fights viruses", "gps works",
              "public key encryption works", "the electoral college works", "black holes form"],
    "a": ["python", "react", "an iphone", "a mac", "renting", "solar panels", "linux", "postgres", "a roth ira",
          "an electric car", "aws", "java"],
    "b": ["javascript", "vue", "an android phone", "a windows pc", "buying a house", "a heat pump", "windows",
          "mongodb", "a 401k", "a hybrid", "azure", "c#"],
    "who2": ["a beginner", "a small business", "a family of four", "a student", "a retired person", "a startup",
             "someone with back pain", "a marathon runner", "a teenager"],
    "plang": ["python", "javascript", "java", "c++", "c#", "go", "rust", "bash", "powershell", "sql", "php"],
    "task": ["reads a csv file and finds the average", "renames photos by date", "sorts a list of dictionaries",
             "downloads all images from a page", "checks if a string is a palindrome", "parses json from an api",
             "merges two sorted arrays", "sends an email every morning", "removes duplicate lines from a file",
             "counts words in a text file", "backs up a folder to a zip file", "connects to a database"],
    "days": ["3", "5", "7", "10", "14", "30"],
    "country": ["japan", "italy", "thailand", "morocco", "peru", "norway", "vietnam", "turkey", "iceland"],
    "plan": ["workout plan", "meal plan", "study plan", "budget plan", "marketing plan", "training schedule",
             "reading plan", "savings plan"],
    "mathq": ["find the derivative of x^2 * ln(x)", "integrate sin(x)^2 dx", "solve 2x^2 - 5x + 3 = 0",
              "prove that there are infinitely many primes", "what is the probability of getting two sixes in "
              "three dice rolls, explain", "find the eigenvalues of the matrix [[2,1],[1,2]]",
              "solve the system x + y = 10 and 2x - y = 5 with steps", "explain the limit of sin(x)/x as x goes to 0",
              "a train leaves at 3pm going 60 mph and another at 4pm going 80 mph, when does the second catch up"],
}


def build(seeds, templates, slots, extra=()):
    gen = fill(templates, slots, PER_CLASS - len(seeds) - len(extra))
    rows = sorted({noisy(s) for s in list(seeds) + gen} | set(extra))
    return [r for r in rows if r.strip()]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sets = {
        "local_chat": build(LOCAL_SEEDS, LOCAL_T, LOCAL_SLOTS),
        "web_search": build(WEB_SEEDS, WEB_T, WEB_SLOTS),
        "online_llm": build(ONLINE_SEEDS, ONLINE_T, ONLINE_SLOTS, ONLINE_CODE),
    }
    for name, rows in sets.items():
        header = f"# {name.upper()} training examples - generated by scripts/make_router_data.py; one per line, \\n = newline\n"
        (OUT / f"{name}.txt").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        print(f"{name}: {len(rows)}")


if __name__ == "__main__":
    main()
